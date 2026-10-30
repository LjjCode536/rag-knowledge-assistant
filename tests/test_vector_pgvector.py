"""单元接缝:pgvector 向量库与内存基线的一致性(M3 三级存储的第三层 / 生产形态)。

同数据同查询下必须与 :class:`~app.repositories.vector_memory.InMemoryVectorStore`
给出同一结果(差异仅浮点精度,来自 Postgres vector 物化为 float32);
检索前元数据过滤(权限维度)在相似度计算之前,由 SQL WHERE 下推完成。
后端不可达(无 Docker)时整批跳过:test_pg_database 夹具负责。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.domain.chunks import Chunk
from app.domain.retrieval import ChunkFilter
from app.repositories.postgres import PgDatabase
from app.repositories.vector_memory import InMemoryVectorStore
from app.repositories.vector_pgvector import PgVectorStore


@pytest.fixture
def pg_store(test_pg_database) -> Iterator[PgVectorStore]:
    db = PgDatabase(test_pg_database.dsn)
    yield PgVectorStore(db)
    db.close()


def _chunk(
    content: str,
    *,
    document_id: str = "doc-1",
    index: int = 0,
    tenant: str = "acme",
    department: str | None = None,
    doc_type: str | None = None,
    version: str = "v1",
) -> Chunk:
    return Chunk(
        document_id=document_id,
        index=index,
        content=content,
        tenant=tenant,
        department=department,
        doc_type=doc_type,
        version=version,
        element_index_start=0,
        element_index_end=0,
    )


def test_search_ranks_by_cosine_similarity_desc(pg_store):
    """与内存基线同题同序同分:丙 > 甲 > 乙(手工余弦,教材第 42 章生产路线)。"""
    pg_store.replace(
        "doc-1",
        [_chunk("甲"), _chunk("乙", index=1), _chunk("丙", index=2)],
        [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
    )

    hits = pg_store.search([1.0, 0.5], top_k=3, filters=ChunkFilter())

    assert [hit.chunk.content for hit in hits] == ["丙", "甲", "乙"]
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0].score == pytest.approx(0.94868, abs=1e-4)
    assert hits[1].score == pytest.approx(0.89443, abs=1e-4)
    assert hits[2].score == pytest.approx(0.44721, abs=1e-4)


def test_scores_and_order_match_memory_baseline(pg_store):
    """同数据同查询:pgvector 与内存基线给出同一 score 序列(仅 float32 精度差)。"""
    corpus = [
        ("甲", [1.0, 0.1]),
        ("乙", [0.95, 1.0]),
        ("丙", [0.0, 1.0]),
        ("丁", [0.5, 0.5]),
        ("戊", [-1.0, 0.2]),
    ]

    memory = InMemoryVectorStore()
    memory.replace(
        "doc-1", [_chunk(t, index=i) for i, (t, _) in enumerate(corpus)], [v for _, v in corpus]
    )
    pg_store.replace(
        "doc-1", [_chunk(t, index=i) for i, (t, _) in enumerate(corpus)], [v for _, v in corpus]
    )

    for query in ([1.0, 0.5], [0.1, 1.0], [-0.3, 0.8]):
        memory_hits = memory.search(query, top_k=10, filters=ChunkFilter())
        pg_hits = pg_store.search(query, top_k=10, filters=ChunkFilter())
        assert [h.chunk.content for h in pg_hits] == [h.chunk.content for h in memory_hits]
        for c, m in zip(pg_hits, memory_hits, strict=True):
            assert c.score == pytest.approx(m.score, abs=1e-5)


def test_metadata_filter_applies_before_similarity_ranking(pg_store):
    """先过滤后排序(拓扑不变量):被过滤掉的更高分候选不得占用 top-k 名额。"""
    pg_store.replace(
        "doc-1",
        [
            _chunk("甲", tenant="acme"),
            _chunk("乙", tenant="beta", index=1),
            _chunk("丙", tenant="acme", index=2),
        ],
        [[1.0, 0.1], [0.95, 1.0], [0.0, 1.0]],
    )

    hits = pg_store.search([1.0, 0.5], top_k=2, filters=ChunkFilter(tenant="acme"))

    assert [hit.chunk.content for hit in hits] == ["甲", "丙"]
    assert all(hit.chunk.tenant == "acme" for hit in hits)
    assert hits[0].score == pytest.approx(0.93449, abs=1e-4)
    assert hits[1].score == pytest.approx(0.44721, abs=1e-4)


def test_filter_matches_all_set_fields_and_leaves_unset_fields_open(pg_store):
    """给定字段全等才入选;未给字段一律放行(department 未填写的丙也在内)。"""
    pg_store.replace(
        "doc-1",
        [
            _chunk("甲", tenant="acme", department="人力", doc_type="政策", version="v2"),
            _chunk("乙", tenant="acme", department="研发", index=1),
            _chunk("丙", tenant="acme", index=2),
        ],
        [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
    )

    hits = pg_store.search(
        [1.0, 0.0], top_k=10, filters=ChunkFilter(tenant="acme", department="人力")
    )
    assert [hit.chunk.content for hit in hits] == ["甲"]

    hits = pg_store.search([1.0, 0.0], top_k=10, filters=ChunkFilter(tenant="acme"))
    assert [hit.chunk.content for hit in hits] == ["甲", "乙", "丙"]

    pg_store.replace("doc-2", [_chunk("丁", document_id="doc-2")], [[1.0, 0.0]])
    hits = pg_store.search([1.0, 0.0], top_k=10, filters=ChunkFilter(document_id="doc-2"))
    assert [hit.chunk.content for hit in hits] == ["丁"]


def test_replace_is_full_swap_for_one_document(pg_store):
    """重嵌即整组替换:旧向量与新切片不并存(ADR-0002 的向量化前提)。"""
    pg_store.replace(
        "doc-1", [_chunk("旧一"), _chunk("旧二", index=1)], [[1.0, 0.0], [1.0, 0.0]]
    )
    pg_store.replace("doc-2", [_chunk("另册", document_id="doc-2")], [[1.0, 0.9]])

    pg_store.replace("doc-1", [_chunk("新一")], [[0.0, 1.0]])

    hits = pg_store.search([1.0, 1.0], top_k=10, filters=ChunkFilter())
    # 旧切片(旧一 / 旧二)整批作废;doc-2 的条目不受影响
    assert [hit.chunk.content for hit in hits] == ["另册", "新一"]


def test_full_metadata_roundtrip(pg_store):
    """检索结果还原完整切片:department / doc_type / 页码 / 标题一个不少。"""
    chunk = Chunk(
        document_id="doc-1",
        index=2,
        content="提拔任用管理办法",
        tenant="acme",
        department="人力",
        doc_type="制度",
        version="v3",
        page_number=7,
        title="任用条件",
        element_index_start=11,
        element_index_end=19,
    )
    pg_store.replace("doc-1", [chunk], [[1.0, 0.0, 0.0]])

    hits = pg_store.search([1.0, 0.0, 0.0], top_k=5, filters=ChunkFilter())

    assert len(hits) == 1
    assert hits[0].chunk == chunk  # pydantic 全字段逐项相等
    assert pg_store.vector_of(chunk.chunk_id) == pytest.approx([1.0, 0.0, 0.0], abs=1e-5)


def test_vector_of_missing_returns_none(pg_store):
    pg_store.replace("doc-1", [_chunk("在册")], [[1.0]])
    assert pg_store.vector_of("missing-chunk-id") is None


def test_top_k_larger_than_candidates_and_empty_store(pg_store):
    assert pg_store.search([1.0], top_k=5, filters=ChunkFilter()) == []

    pg_store.replace("doc-1", [_chunk("唯一")], [[1.0]])
    hits = pg_store.search([1.0], top_k=5, filters=ChunkFilter())
    assert [hit.chunk.content for hit in hits] == ["唯一"]


def test_zero_vector_scores_zero_without_crashing(pg_store):
    pg_store.replace("doc-1", [_chunk("零向量")], [[0.0, 0.0]])

    hits = pg_store.search([1.0, 1.0], top_k=5, filters=ChunkFilter())
    assert len(hits) == 1
    assert hits[0].score == 0.0


def test_delete_is_idempotent_across_stores(pg_store):
    pg_store.replace("doc-1", [_chunk("待删")], [[1.0, 0.0]])
    pg_store.delete("doc-1")

    assert pg_store.search([1.0, 0.0], top_k=5, filters=ChunkFilter()) == []
    pg_store.delete("doc-1")  # 未入库幂等
    pg_store.delete("从未见过")  # 自始未入库同样幂等

    # 其他文档不受连带
    pg_store.replace("doc-2", [_chunk("留存", document_id="doc-2")], [[1.0, 0.0]])
    hits = pg_store.search([1.0, 0.0], top_k=5, filters=ChunkFilter())
    assert [hit.chunk.content for hit in hits] == ["留存"]


def test_reopening_same_database_keeps_vectors(test_pg_database):
    """持久化:重开连接(同 DSN)后向量仍在,重启进程 / 容器数据不丢(验收口径)。"""
    first = PgDatabase(test_pg_database.dsn)
    try:
        PgVectorStore(first).replace("doc-1", [_chunk("存档")], [[1.0, 0.0]])
    finally:
        first.close()

    second = PgDatabase(test_pg_database.dsn)
    try:
        hits = PgVectorStore(second).search([1.0, 0.0], top_k=5, filters=ChunkFilter())
        assert [hit.chunk.content for hit in hits] == ["存档"]
    finally:
        second.close()


def test_mixed_dimension_column_degrades_without_crashing(pg_store):
    """迁移期混维共存:同库里新维度与旧维度向量并存,检索按 0 分降级不崩溃,
    且结果不超 top_k(整库重嵌前的过渡窗口)。"""
    pg_store.replace(
        "doc-1",
        [_chunk("旧一维"), _chunk("旧二维", index=1)],
        [[1.0], [1.0, 1.0]],
    )
    pg_store.replace("doc-2", [_chunk("三维", document_id="doc-2")], [[1.0, 1.0, 1.0]])

    hits = pg_store.search([1.0, 1.0, 1.0], top_k=2, filters=ChunkFilter())

    assert len(hits) == 2  # 至多 top_k
    assert hits[0].chunk.content == "三维"
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)
    # 维度不一致的两条旧向量不参与有效相似度,同按 0 分降级
    assert hits[1].score == 0.0

    # 查询维度也不匹配:全部候选按 0 分降级,不 5xx
    mismatches = pg_store.search([1.0, 1.0, 1.0, 1.0], top_k=1, filters=ChunkFilter())
    assert len(mismatches) == 1
    assert mismatches[0].score == 0.0
