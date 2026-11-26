"""向量库的 pgvector 实现:M3 三级存储(内存 → Chroma → pgvector)的第三层。

生产持久化形态:向量与切片元数据快照落在 PostgreSQL 16 + pgvector,重启进程 /
容器数据不丢;与内存基线同一契约(:class:`~app.domain.retrieval.VectorStore`),
应用装配与高级检索零改动(ADR-0001)。

一致性口径(与 Chroma 实现逐条对齐,docs/iterations 有切换前后对比):

- 相似度分数不用 Postgres 原生距离:候选一律在 Python 用领域层
  :func:`~app.domain.retrieval.cosine_similarity` 复算,三个后端(内存 /
  Chroma / pgvector)分数同源,仅剩 vector 列物化为 float32 的 1e-5 级精度差;
- 来源身份(模型 / 版本 / 维度)随每个向量落库,检索前作为 SQL 条件下推:
  不同模型 / 版本的向量绝不互算分数(ADR-0019);身份未知的历史行(NULL)
  不参与检索,等回填脚本补齐后自动恢复可见;
- 检索前过滤下推 SQL WHERE(embeddings 自带的切片元数据列),先筛候选、
  再算相似度;元数据字段空值(NULL)不参与等值过滤,与内存基线
  ``None != value`` 语义一致;
- 索引列不带维度修饰,维度正确性由写入侧断言(DimensionMismatchError)与
  ``embedding_dimension`` 过滤双重保证:同一身份的行维度必然一致,因此不
  需要按查询维度做降级兜底;定维列 + HNSW 留给独立脚本(ADR-0019)。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg.rows import dict_row

from app.domain.chunks import Chunk
from app.domain.retrieval import (
    EmbeddedChunk,
    EmbeddingRef,
    Scope,
    ScoredChunk,
    consistent_embedding_ref,
    cosine_similarity,
)
from app.repositories.postgres.base import PgDatabase

_SEARCH_COLUMNS = (
    "e.chunk_id, e.document_id, e.chunk_index, e.content, e.tenant, "
    "e.department, e.doc_type, e.version, e.page_number, e.title, "
    "e.element_index_start, e.element_index_end, e.embedding"
)

# 过滤字段 → chunks / embeddings 列名:契约字段名同名直取。
# 字段集由领域模型推导 —— 给 Scope 加字段时不会静默漏掉 SQL 条件
# (推导式会直接报出"无对应列"的程序错误,而不是运行时 KeyError)。
_FILTER_COLUMNS = {
    field: field
    for field in Scope.model_fields
    if field in {
        "tenant",
        "department",
        "doc_type",
        "version",
        "document_id",
    }
}
# 领域模型里有、但 embeddings 表没有对应列的等值过滤字段:程序错误(装配期暴露)。
# ``document_ids`` 是集合条件,由 ``_where_of`` 单独翻译成 IN,不走等值映射。
_UNMAPPED_FILTER_FIELDS = set(Scope.model_fields) - set(_FILTER_COLUMNS) - {
    "document_ids"
}
if _UNMAPPED_FILTER_FIELDS:
    raise RuntimeError(
        "Scope 存在无对应列的过滤字段(embeddings 表缺列):"
        + ", ".join(sorted(_UNMAPPED_FILTER_FIELDS))
    )


def _to_pg_vector(vector: Sequence[float]) -> str:
    """float 序列 → pgvector 文本形态 ``[a,b,c]``(cast 入列)。

    文本形态是 psycopg3 走 pgvector 的既定通道(``::vector`` cast);
    浮点用 ``str`` 输出为最短往返表示,精度不丢。
    """
    return "[" + ",".join(str(float(component)) for component in vector) + "]"


def _as_vector(stored: str | None) -> list[float] | None:
    """pgvector 文本输出(如 ``[1,0.5]``)→ float 列表;缺结果按 None。"""
    if stored is None:
        return None
    return [float(component) for component in stored.strip("[]").split(",") if component]


def _where_of(scope: Scope) -> tuple[str, list[Any]]:
    """生效授权范围 → SQL WHERE 片段(等值条件 + 文档集合 IN)。"""
    constraints = scope.effective()
    clauses: list[str] = []
    params: list[Any] = []
    for field, value in constraints.items():
        column = _FILTER_COLUMNS[field]
        clauses.append(f"{column} = %s")
        params.append(value)
    if scope.document_ids is not None:
        clauses.append("e.document_id = ANY(%s::text[])")
        params.append(list(scope.document_ids))
    return " AND ".join(clauses) if clauses else "true", params


def _identity_of(embedding: EmbeddingRef) -> tuple[str, list[Any]]:
    """来源身份 → SQL 条件:模型 / 版本 / 维度三者全等才进候选。

    身份未知的历史行(NULL)不会被 ``=`` 命中:检索里"消失"直到回填,
    而不是与当前模型的向量混算。
    """
    return (
        "embedding_model = %s AND embedding_version = %s AND embedding_dimension = %s",
        [embedding.model, embedding.version, embedding.dimension],
    )


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
    """pgvector 向量库:embedding + 切片元数据快照 + 来源身份同表存续,按文档整组联动。

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
        chunks: Sequence[EmbeddedChunk],
    ) -> None:
        """整组写入一个文档的切片向量;混入不同来源身份的批次视为程序错误。"""
        consistent_embedding_ref(chunks)
        insert = (
            "INSERT INTO embeddings (chunk_id, document_id, embedding, "
            "embedding_model, embedding_version, embedding_dimension, chunk_index, "
            "content, tenant, department, doc_type, version, page_number, title, "
            "element_index_start, element_index_end) "
            "VALUES (%s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        with self._db.connection() as conn, conn.transaction():
            self._delete_only(document_id, conn)
            for entry in chunks:
                chunk = entry.chunk
                conn.execute(
                    insert,
                    (
                        chunk.chunk_id,
                        document_id,
                        _to_pg_vector(entry.vector),
                        entry.embedding.model,
                        entry.embedding.version,
                        entry.embedding.dimension,
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
        scope: Scope,
        embedding: EmbeddingRef,
    ) -> list[ScoredChunk]:
        """身份与授权范围过滤都在相似度计算之前:越界切片不进 SQL 候选。"""
        if scope.document_ids is not None and not scope.document_ids:
            return []
        where, params = _where_of(scope)
        identity, identity_params = _identity_of(embedding)
        query = (
            "SELECT " + _SEARCH_COLUMNS + f" FROM embeddings e WHERE {where} "
            f"AND {identity} ORDER BY e.embedding <=> %s::vector LIMIT %s"
        )
        sql_params: list[Any] = [
            *params,
            *identity_params,
            _to_pg_vector(vector),
            top_k,
        ]
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(query, sql_params).fetchall()
        hits = [
            ScoredChunk(
                chunk=_chunk_of(row),
                score=cosine_similarity(vector, _as_vector(row["embedding"]) or []),
            )
            for row in rows
        ]
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    def vector_of(self, chunk_id: str) -> Sequence[float] | None:
        """取一个切片的已存向量(MMR 需要候选间的多样性计算);缺失返回 None。"""
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT embedding FROM embeddings WHERE chunk_id = %s", (chunk_id,)
            ).fetchone()
        if row is None:
            return None
        return _as_vector(row[0])

    def identity_counts(self) -> dict[EmbeddingRef | None, int]:
        """库内向量按来源身份计数(启动期一致性告警用)。"""
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT embedding_model, embedding_version, embedding_dimension, count(*) "
                "FROM embeddings GROUP BY 1, 2, 3"
            ).fetchall()
        counts: dict[EmbeddingRef | None, int] = {}
        for model, version, dimension, count in rows:
            ref = (
                None
                if model is None or version is None or dimension is None
                else EmbeddingRef(model=model, version=version, dimension=int(dimension))
            )
            counts[ref] = counts.get(ref, 0) + int(count)
        return counts
