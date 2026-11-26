"""单元接缝:内存向量库的相似度排序、检索前元数据过滤与身份隔离(M3 第一层基线)。"""

from __future__ import annotations

import pytest

from app.domain.chunks import Chunk
from app.domain.retrieval import (
    EmbeddingRef,
    Scope,
    build_embedded_chunks,
)
from app.repositories.vector_memory import InMemoryVectorStore


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


def _ref(dim: int = 2, *, model: str = "stub", version: str = "v1") -> EmbeddingRef:
    return EmbeddingRef(model=model, version=version, dimension=dim)


def _embedded(chunks, vectors, *, ref: EmbeddingRef | None = None):
    return build_embedded_chunks(chunks, vectors, ref or _ref(len(vectors[0])))


def test_search_ranks_by_cosine_similarity_desc():
    store = InMemoryVectorStore()
    store.replace(
        "doc-1",
        _embedded(
            [_chunk("甲"), _chunk("乙"), _chunk("丙", index=1)],
            [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
        ),
    )

    hits = store.search([1.0, 0.5], top_k=3, scope=Scope(tenant="acme"), embedding=_ref())

    assert [hit.chunk.content for hit in hits] == ["丙", "甲", "乙"]
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)  # 同题查询按分数降序
    # 手工余弦:丙 1.5/(√2·√1.25)、甲 1/√1.25、乙 0.5/√1.25
    assert scores[0] == pytest.approx(0.94868, abs=1e-4)
    assert scores[1] == pytest.approx(0.89443, abs=1e-4)
    assert scores[2] == pytest.approx(0.44721, abs=1e-4)


def test_metadata_filter_applies_before_similarity_ranking():
    """先过滤后排序:被过滤掉的更高分候选不得占用 top-k 名额。

    甲(租户 A,相似度 0.9357)、乙(租户 B,相似度 0.9402,全场最高)、
    丙(租户 A,相似度 0.4472)。tenant=A + top_k=2 应返回甲、丙两片;
    若把过滤放在检索之后,丙会被截断,只返回一片。
    """
    store = InMemoryVectorStore()
    store.replace(
        "doc-1",
        _embedded(
            [
                _chunk("甲", tenant="acme"),
                _chunk("乙", tenant="beta", index=1),
                _chunk("丙", tenant="acme", index=2),
            ],
            [[1.0, 0.1], [0.95, 1.0], [0.0, 1.0]],
        ),
    )

    hits = store.search(
        [1.0, 0.5], top_k=2, scope=Scope(tenant="acme"), embedding=_ref()
    )

    assert [hit.chunk.content for hit in hits] == ["甲", "丙"]
    assert all(hit.chunk.tenant == "acme" for hit in hits)
    assert hits[0].score == pytest.approx(0.93449, abs=1e-4)
    assert hits[1].score == pytest.approx(0.44721, abs=1e-4)


def test_filter_matches_all_set_fields_and_leaves_unset_fields_open():
    store = InMemoryVectorStore()
    store.replace(
        "doc-1",
        _embedded(
            [
                _chunk("甲", tenant="acme", department="人力", doc_type="政策", version="v2"),
                _chunk("乙", tenant="acme", department="研发", index=1),
                _chunk("丙", tenant="acme", index=2),  # department 未填写
            ],
            [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
        ),
    )

    hits = store.search(
        [1.0, 0.0],
        top_k=10,
        scope=Scope(tenant="acme", department="人力"),
        embedding=_ref(),
    )
    assert [hit.chunk.content for hit in hits] == ["甲"]

    # 未给过滤字段一律放行(department=None 的丙也在内)
    hits = store.search(
        [1.0, 0.0], top_k=10, scope=Scope(tenant="acme"), embedding=_ref()
    )
    assert [hit.chunk.content for hit in hits] == ["甲", "乙", "丙"]

    store.replace("doc-2", _embedded([_chunk("丁", document_id="doc-2")], [[1.0, 0.0]]))
    hits = store.search(
        [1.0, 0.0],
        top_k=10,
        scope=Scope(tenant="acme", document_id="doc-2"),
        embedding=_ref(),
    )
    assert [hit.chunk.content for hit in hits] == ["丁"]


def test_replace_is_full_swap_for_one_document():
    """重嵌即整组替换:旧向量与新切片不至于并存(ADR-0002 的向量化前提)。"""
    store = InMemoryVectorStore()
    store.replace(
        "doc-1", _embedded([_chunk("旧一"), _chunk("旧二", index=1)], [[1.0, 0.0], [1.0, 0.0]])
    )
    store.replace("doc-2", _embedded([_chunk("另册", document_id="doc-2")], [[1.0, 0.0]]))

    store.replace("doc-1", _embedded([_chunk("新一")], [[0.0, 1.0]]))

    hits = store.search([1.0, 1.0], top_k=10, scope=Scope(tenant="acme"), embedding=_ref())
    # 旧切片(旧一 / 旧二)整批作废;doc-2 的条目不受影响
    assert [hit.chunk.content for hit in hits] == ["新一", "另册"]


def test_top_k_larger_than_candidates_returns_all_and_empty_store_returns_empty():
    store = InMemoryVectorStore()
    assert store.search([1.0], top_k=5, scope=Scope(tenant="acme"), embedding=_ref(1)) == []

    store.replace("doc-1", _embedded([_chunk("唯一")], [[1.0]]))
    hits = store.search([1.0], top_k=5, scope=Scope(tenant="acme"), embedding=_ref(1))
    assert [hit.chunk.content for hit in hits] == ["唯一"]


def test_zero_vector_scores_zero_without_crashing():
    store = InMemoryVectorStore()
    store.replace("doc-1", _embedded([_chunk("零向量")], [[0.0, 0.0]]))

    hits = store.search([1.0, 1.0], top_k=5, scope=Scope(tenant="acme"), embedding=_ref())
    assert len(hits) == 1
    assert hits[0].score == 0.0


def test_other_identity_is_invisible_not_scored_zero():
    """同维度但不同模型 / 版本的向量不参与互算:检索里直接消失(ADR-0019)。"""
    store = InMemoryVectorStore()
    store.replace(
        "doc-1",
        _embedded([_chunk("旧模型")], [[1.0, 0.0]], ref=_ref(2, model="old-model")),
    )

    assert (
        store.search([1.0, 0.0], top_k=5, scope=Scope(tenant="acme"), embedding=_ref())
        == []
    )
    # 换成旧身份查询即可命中:隔离是双向的,不偏袒当前配置
    hits = store.search(
        [1.0, 0.0],
        top_k=5,
        scope=Scope(tenant="acme"),
        embedding=_ref(2, model="old-model"),
    )
    assert [hit.chunk.content for hit in hits] == ["旧模型"]


def test_other_version_is_invisible():
    """同一模型换版本(预处理口径变化)也是不同索引空间,不互算。"""
    store = InMemoryVectorStore()
    store.replace("doc-1", _embedded([_chunk("已知")], [[1.0, 0.0]]))
    store.replace(
        "doc-2",
        _embedded([_chunk("未知", document_id="doc-2")], [[1.0, 0.0]], ref=_ref(2, version="v0")),
    )

    hits = store.search([1.0, 0.0], top_k=10, scope=Scope(tenant="acme"), embedding=_ref())
    assert [hit.chunk.content for hit in hits] == ["已知"]


def test_document_set_filter_is_before_scoring_and_empty_set_is_zero_hits():
    """集合条件先于打分;空集合零命中,绝不退化成"不限制"(ADR-0021)。"""
    store = InMemoryVectorStore()
    store.replace(
        "doc-1",
        _embedded([_chunk("甲", document_id="doc-1")], [[1.0, 0.0]]),
    )
    store.replace(
        "doc-2",
        _embedded([_chunk("乙", document_id="doc-2")], [[1.0, 0.0]]),
    )
    store.replace(
        "doc-3",
        _embedded(
            [_chunk("丙", document_id="doc-3", department="人力")], [[1.0, 0.0]]
        ),
    )

    only_doc_2 = store.search(
        [1.0, 0.0],
        top_k=10,
        scope=Scope(tenant="acme", document_ids=frozenset({"doc-2"})),
        embedding=_ref(),
    )
    assert [hit.chunk.document_id for hit in only_doc_2] == ["doc-2"]

    combined = store.search(
        [1.0, 0.0],
        top_k=10,
        scope=Scope(
            tenant="acme",
            department="人力",
            document_ids=frozenset({"doc-1", "doc-2", "doc-3"}),
        ),
        embedding=_ref(),
    )
    assert [hit.chunk.document_id for hit in combined] == ["doc-3"]

    assert (
        store.search(
            [1.0, 0.0],
            top_k=10,
            scope=Scope(tenant="acme", document_ids=frozenset()),
            embedding=_ref(),
        )
        == []
    )


def test_identity_counts_groups_by_source_identity():
    store = InMemoryVectorStore()
    store.replace("doc-1", _embedded([_chunk("甲"), _chunk("乙", index=1)], [[1.0], [1.0]]))
    store.replace(
        "doc-2",
        _embedded([_chunk("丙", document_id="doc-2")], [[1.0]], ref=_ref(1, model="other")),
    )

    assert store.identity_counts() == {_ref(1): 2, _ref(1, model="other"): 1}
