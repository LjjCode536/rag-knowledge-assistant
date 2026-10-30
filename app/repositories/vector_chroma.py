"""向量库的 Chroma 实现:M3 三级存储(内存 → Chroma → pgvector)的第二层(教材第 42 章)。

持久化形态:一个数据目录一份标签,重启不重嵌;与内存基线同一契约
(:class:`~app.domain.retrieval.VectorStore`)实现,应用装配与高级检索零改动。

与内存基线的一致性约定(docs/iterations 有切换前后对比报告):

- 相似度分数不用 Chroma 原生距离:对查询向量与取回向量复算领域层
  :func:`~app.domain.retrieval.cosine_similarity`,内存 / Chroma 两侧同源,
  仅剩向量物化为 float32 的 1e-5 级精度差;
- 检索前过滤下推到 Chroma 的 ``where`` 原生查询,被过滤的切片不进 ANN
  候选,同内存基线"先筛候选、再算相似度"的口径;
- 元数据不含空值字段:未填写的字段不写入,``$eq`` 对缺失字段按不匹配
  处理,与内存基线 ``None != value`` 语义一致;
- 维度不一致按 0 分降级(不参与有效相似度),与内存基线同语义;
  切片向量写入维度不一致仍然报错(换向量模型必须整库重嵌,ADR-0003)。

Chroma 不设默认嵌入函数:调用方显式传入与查询一致的向量;遥测关闭,
评估与测试不触网。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from threading import Lock
from typing import Any

from app.domain.chunks import Chunk
from app.domain.retrieval import ChunkFilter, ScoredChunk, cosine_similarity

# 切片元数据里可能为空的字段:为 None 时不写入,取回时按 None 还原
_OPTIONAL_METADATA_FIELDS = ("department", "doc_type", "page_number", "title")
# 必填切片元数据:写入与还原都靠同名键
_REQUIRED_METADATA_FIELDS = (
    "document_id",
    "index",
    "content",
    "tenant",
    "version",
    "element_index_start",
    "element_index_end",
)


class ChromaVectorStore:
    """Chroma 向量库:一个持久目录一份标签,重嵌即整组替换,读写加锁。

    与内存基线同契约:同一文档整组写入;``delete`` 按文档连带清理;
    ``search`` 结果按相似度降序(同分顺序视 Chroma 返回序,内存基线为
    写入序,属可解释差异)。
    """

    def __init__(self, *, persist_dir: Path) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self._persist_dir = persist_dir
        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # 余弦空间标签:分数口径与内存基线的余弦相似度一致(越大越相关)
        self._collection = self._client.get_or_create_collection(
            name="knowledge_chunks", metadata={"hnsw:space": "cosine"}
        )
        self._lock = Lock()

    def close(self) -> None:
        """释放 Chroma 客户端持有的文件句柄(临时目录与样例复现的清理钩子)。"""
        self._client.close()

    def replace(
        self,
        document_id: str,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        """整组写入一个文档的切片向量;切片数与向量数不对齐视为程序错误。"""
        if len(chunks) != len(vectors):
            raise ValueError(
                f"切片数({len(chunks)})与向量数({len(vectors)})不对齐:{document_id}"
            )
        with self._lock:
            self._delete_only(document_id)
            if chunks:
                self._collection.add(
                    ids=[chunk.chunk_id for chunk in chunks],
                    embeddings=[list(vector) for vector in vectors],
                    metadatas=[_metadata_of(chunk) for chunk in chunks],
                )

    def delete(self, document_id: str) -> None:
        """整批清掉一个文档的全部向量(版本退位 / 删除的连带清理),未入库幂等。"""
        with self._lock:
            self._delete_only(document_id)

    def _delete_only(self, document_id: str) -> None:
        self._collection.delete(where={"document_id": {"$eq": document_id}})

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        filters: ChunkFilter | None,
    ) -> list[ScoredChunk]:
        """过滤在相似度计算之前:where 条件下推,被过滤的切片不进 ANN 候选。"""
        constraints = (filters or ChunkFilter()).effective()
        where = _chroma_where(constraints)
        with self._lock:
            try:
                result = self._collection.query(
                    query_embeddings=[list(vector)],
                    n_results=top_k,
                    where=where,
                    include=["metadatas", "embeddings"],
                )
                metadatas = _query_flat(result, "metadatas")
                embeddings = _query_flat(result, "embeddings")
                record_ids = _query_flat(result, "ids")
                hits = []
                for metadata, embedding, record_id in zip(
                    metadatas, embeddings, record_ids, strict=False
                ):
                    stored = _as_vector(embedding)
                    if stored is not None:
                        hits.append(
                            ScoredChunk(
                                chunk=_chunk_of(metadata, record_id),
                                score=cosine_similarity(vector, stored),
                            )
                        )
            except Exception as exc:
                if not _is_dimension_mismatch(exc):
                    raise
                # 维度不一致(如换向量模型后未重嵌):同内存基线按 0 分降级,
                # 不参与有效相似度,检索接口不因此 5xx。
                hits = self._fallback_zero_hits(where, top_k)
        # 稳定按分数降序:绕开 Chroma 内部 ANN 的同分顺序浮动
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits

    def vector_of(self, chunk_id: str) -> Sequence[float] | None:
        """取一个切片的已存向量(MMR 需要候选间的多样性计算);缺失返回 None。"""
        with self._lock:
            result = self._collection.get(ids=[chunk_id], include=["embeddings"])
        stored = result.get("embeddings")
        # get 结果本就是平层:至多一个切片的向量数组,没有即缺失
        stored = stored[0] if stored is not None and len(stored) > 0 else None
        return _as_vector(stored)

    def _fallback_zero_hits(self, where: dict[str, Any] | None, top_k: int) -> list[ScoredChunk]:
        """维度不一致时的降级候选:满足过滤条件的切片全部按 0 分返回。"""
        result = self._collection.get(where=where, include=["metadatas"])
        metadatas = result.get("metadatas") or []
        record_ids = result.get("ids") or []
        return [
            ScoredChunk(chunk=_chunk_of(metadata, record_id), score=0.0)
            for metadata, record_id in zip(metadatas[:top_k], record_ids[:top_k], strict=False)
        ]


def _is_dimension_mismatch(exc: Exception) -> bool:
    """识别 Chroma 自家错误类型里的维度报错;其余异常一律原样上抛,不静默吞没。"""
    import chromadb.errors

    if not isinstance(exc, chromadb.errors.InvalidArgumentError):
        return False
    return "dimension" in str(exc).lower()


def _metadata_of(chunk: Chunk) -> dict[str, Any]:
    """切片 → Chroma 元数据:None 字段不写入(缺失即未填写)。"""
    metadata: dict[str, Any] = {field: getattr(chunk, field) for field in _REQUIRED_METADATA_FIELDS}
    for field in _OPTIONAL_METADATA_FIELDS:
        value = getattr(chunk, field)
        if value is not None:
            metadata[field] = value
    return metadata


def _chunk_of(metadata: dict[str, Any], record_id: str) -> Chunk:
    """Chroma 记录(主键即 chunk_id)与元数据 → 切片:缺失键按未填写(None)还原。"""
    fields: dict[str, Any] = {"chunk_id": record_id}
    for field in _REQUIRED_METADATA_FIELDS + _OPTIONAL_METADATA_FIELDS:
        value = metadata.get(field)
        if value is not None:
            fields[field] = value
    return Chunk(**fields)


def _chroma_where(constraints: dict[str, str]) -> dict[str, Any] | None:
    """过滤条件 → Chroma where:单条直接等值,多条 $and,空条件放行。"""
    conditions = [{field: {"$eq": value}} for field, value in constraints.items()]
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _query_flat(result: dict[str, Any], key: str) -> list[Any]:
    """Chroma query 结果拆包:外层是按查询的列表(恒为 1 条),降为平层。"""
    values = result.get(key)
    if not values:
        return []
    return list(values[0]) if values[0] is not None else []


def _as_vector(stored: Any) -> Sequence[float] | None:
    """Chroma 向量(numpy float32 数组)→ float 序列;缺结果按 None。"""
    if stored is None or len(stored) == 0:
        return None
    return [float(x) for x in stored]
