"""向量库的 pgvector 实现:M3 三级存储(内存 → Chroma → pgvector)的第三层。

生产持久化形态:向量与切片元数据快照落在 PostgreSQL 16 + pgvector,重启进程 /
容器数据不丢;与内存基线同一契约(:class:`~app.domain.retrieval.VectorStore`),
应用装配与高级检索零改动(ADR-0001)。

一致性口径(与 Chroma 实现逐条对齐,docs/iterations 有切换前后对比):

- 相似度分数不用 Postgres 原生距离:候选一律在 Python 用领域层
  :func:`~app.domain.retrieval.cosine_similarity` 复算,三个后端(内存 /
  Chroma / pgvector)分数同源,仅剩 vector 列物化为 float32 的 1e-5 级精度差;
- 检索前过滤下推 SQL WHERE(embeddings 自带的切片元数据列),先筛候选、
  再算相似度;元数据字段空值(NULL)不参与等值过滤,与内存基线
  ``None != value`` 语义一致;
- 维度不一致(如换向量模型后未整库重嵌)在 Python 复算阶段按 0 分降级,不
  参与有效相似度,检索接口不因此 5xx;切片向量列不带维度修饰,允许迁移期
  新旧维度共存(换嵌入模型必须整库重嵌,ADR-0003);
- 候选排序走 SQL 侧精确余弦(``ORDER BY embedding <=> :q``),不建 ANN 索引:
  HNSW 要求定维列,与混维共存语义冲突;内部教材口径的语料量级下精确扫描
  足够,升维定容后可换定维列 + HNSW(留在 ADR 后续迭代)。
"""

from __future__ import annotations

from collections.abc import Sequence

import psycopg
from psycopg.rows import dict_row

from app.domain.chunks import Chunk
from app.domain.retrieval import ChunkFilter, ScoredChunk, cosine_similarity
from app.repositories.postgres.base import PgDatabase

_SEARCH_COLUMNS = (
    "e.chunk_id, e.document_id, e.chunk_index, e.content, e.tenant, "
    "e.department, e.doc_type, e.version, e.page_number, e.title, "
    "e.element_index_start, e.element_index_end, e.embedding"
)

# 过滤字段 → chunks / embeddings 列名:契约字段名同名直取
_FILTER_COLUMNS = {
    "tenant": "tenant",
    "department": "department",
    "doc_type": "doc_type",
    "version": "version",
    "document_id": "document_id",
}


def _to_pg_vector(vector: Sequence[float]) -> str:
    """float 序列 → pgvector 文本形态 ``[a,b,c]``(repr 不丢精度,cast 入列)。"""
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"


def _as_vector(stored: str | None) -> list[float] | None:
    """pgvector 文本输出(如 ``[1,0.5]``)→ float 列表;缺结果按 None。"""
    if stored is None:
        return None
    return [float(component) for component in stored.strip("[]").split(",") if component]


def _where_of(filters: ChunkFilter) -> tuple[str, list[str]]:
    """生效过滤条件 → SQL WHERE 片段(AND 等值);空条件返回 (true, [])。"""
    constraints = filters.effective()
    clauses: list[str] = []
    params: list[str] = []
    for field, value in constraints.items():
        column = _FILTER_COLUMNS[field]
        clauses.append(f"{column} = %s")
        params.append(value)
    return " AND ".join(clauses) if clauses else "true", params


def _chunk_of(row: dict) -> Chunk:
    return Chunk(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        index=row["chunk_index"],
        content=row["content"],
        tenant=row["tenant"],
        department=row["department"],
        doc_type=row["doc_type"],
        version=row["version"],
        page_number=row["page_number"],
        title=row["title"],
        element_index_start=row["element_index_start"],
        element_index_end=row["element_index_end"],
    )


class PgVectorStore:
    """pgvector 向量库:embedding + 切片元数据快照同表存续,按文档整组联动。

    与内存基线同契约:同一文档重嵌即整组替换;``delete`` 按文档连带清理
    (未入库幂等);``search`` 结果按相似度降序。score 复算与 Chroma 同源。
    """

    def __init__(self, db: PgDatabase) -> None:
        self._db = db

    def close(self) -> None:
        """统一关闭钩子:pgvector 模式下仓储与向量库共用同一连接池,由装配点
        调用(应用 lifespan 收尾 / 评估脚本收尾 / 测试清理),幂等。
        """
        self._db.close()

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
        insert = (
            "INSERT INTO embeddings (chunk_id, document_id, embedding, chunk_index, "
            "content, tenant, department, doc_type, version, page_number, title, "
            "element_index_start, element_index_end) "
            "VALUES (%s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        with self._db.connection() as conn, conn.transaction():
            self._delete_only(document_id, conn)
            for chunk, vector in zip(chunks, vectors, strict=True):
                conn.execute(
                    insert,
                    (
                        chunk.chunk_id,
                        document_id,
                        _to_pg_vector(vector),
                        chunk.index,
                        chunk.content,
                        chunk.tenant,
                        chunk.department,
                        chunk.doc_type,
                        chunk.version,
                        chunk.page_number,
                        chunk.title,
                        chunk.element_index_start,
                        chunk.element_index_end,
                    ),
                )

    def delete(self, document_id: str) -> None:
        """整批清掉一个文档的全部向量(版本退位 / 删除的连带清理),未入库幂等。"""
        with self._db.connection() as conn:
            self._delete_only(document_id, conn)

    def _delete_only(self, document_id: str, conn) -> None:
        conn.execute("DELETE FROM embeddings WHERE document_id = %s", (document_id,))

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        filters: ChunkFilter | None,
    ) -> list[ScoredChunk]:
        """过滤在相似度计算之前:WHERE 下推 SQL,被过滤的切片不进候选。"""
        where, params = _where_of(filters or ChunkFilter())
        query_vector = _to_pg_vector(vector)
        ordered = (
            "SELECT " + _SEARCH_COLUMNS + f" FROM embeddings e WHERE {where} "
            "ORDER BY e.embedding <=> %s::vector LIMIT %s"
        )
        unordered = "SELECT " + _SEARCH_COLUMNS + f" FROM embeddings e WHERE {where}"
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            rows = self._fetch_candidates(cursor, ordered, unordered, params, query_vector, top_k)
        hits = [
            ScoredChunk(
                chunk=_chunk_of(row),
                score=cosine_similarity(vector, _as_vector(row["embedding"]) or []),
            )
            for row in rows
        ]
        hits.sort(key=lambda hit: hit.score, reverse=True)
        # 混维降级路径取回的是全量过滤候选,必须切回 top_k(契约:至多 top_k 个)
        return hits[:top_k]

    def _fetch_candidates(
        self, cursor, ordered: str, unordered: str, params: list[str], query_vector: str, top_k: int
    ) -> list[dict]:
        """先按 SQL 侧精确余弦取 top_k 候选;混维(如迁移期)退回全量候选再 Python 复算。"""
        try:
            cursor.execute(ordered, (*params, query_vector, top_k))
            return cursor.fetchall()
        except psycopg.errors.DataError as exc:
            if "dimension" not in str(exc).lower():
                raise
            cursor.execute(unordered, params)
            return cursor.fetchall()

    def vector_of(self, chunk_id: str) -> Sequence[float] | None:
        """取一个切片的已存向量(MMR 需要候选间的多样性计算);缺失返回 None。"""
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT embedding FROM embeddings WHERE chunk_id = %s", (chunk_id,)
            ).fetchone()
        if row is None:
            return None
        return _as_vector(row[0])
