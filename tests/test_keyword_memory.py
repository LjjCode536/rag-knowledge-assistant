"""关键词通道缝:InMemoryKeywordIndex(rank_bm25 零件)+ 检索前过滤契约。

验收口径:
- 词法相关性排序、与向量召回并列成混合检索的第二条通道;
- 权限过滤发生在打分之前:被过滤的切片不占 top-k 名额;
- 与向量库同语义:同一文档重写即整组替换;
- 分词与确定性向量替身同一口径(英文取词、中文二字滑窗)。
"""

from __future__ import annotations

from app.domain.chunks import Chunk
from app.domain.lexical import text_tokens
from app.domain.retrieval import ChunkFilter, ScoredChunk
from app.repositories.keyword_memory import InMemoryKeywordIndex


def _chunk(
    document_id: str,
    index: int,
    content: str,
    *,
    tenant: str = "acme",
    department: str | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}#{index}",
        document_id=document_id,
        index=index,
        content=content,
        tenant=tenant,
        department=department,
        version="v1",
        element_index_start=index * 10,
        element_index_end=index * 10 + 1,
    )


def test_tokens_are_shared_with_deterministic_vector_identity() -> None:
    """分词口径与确定性向量替身一致:同一词面 → 同一 token 序列。"""
    assert text_tokens("没发货可以退钱吗 unshipped refund") == text_tokens(
        "没发货可以退钱吗 unshipped refund"
    )
    assert "unshipped" in text_tokens("two unshipped orders")
    assert text_tokens("退款") == ["退款"]


def test_keyword_search_returns_only_lexically_overlapping_chunks() -> None:
    index = InMemoryKeywordIndex()
    index.replace("alpha", [_chunk("alpha", 0, "年假如何累计与清零")])
    index.replace("beta", [_chunk("beta", 0, "订单尚未发货时,支持全额退款")])

    hits = index.search("没发货可以退款吗", top_k=5, filters=None)

    assert [hit.chunk.chunk_id for hit in hits] == ["beta#0"]


def test_keyword_search_ranks_by_bm25_score_descending() -> None:
    index = InMemoryKeywordIndex()
    # 同 token 命中:文档越短 / 命中越集中分数越高;弱命中排后面
    index.replace(
        "policy",
        [
            _chunk("policy", 0, "未发货时退款流程:订单尚未发货即可全额退款"),
            _chunk("policy", 1, "售后服务包含维修与换新"),
            _chunk("policy", 2, "发货时效:48 小时内发货并同步物流单号"),
        ],
    )

    hits = index.search("未发货退款", top_k=3, filters=None)

    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0].chunk.chunk_id == "policy#0"
    assert scores[0] > scores[-1] > 0
    assert isinstance(hits[0], ScoredChunk)


def test_keyword_channel_permission_filter_is_pre_retrieval() -> None:
    """权限维度在打分之前:被过滤切片不参与排名,也不占 top-k 名额。"""
    index = InMemoryKeywordIndex()
    index.replace(
        "acme_doc", [_chunk("acme_doc", 0, "未发货退款政策", tenant="acme")]
    )
    index.replace(
        "beta_doc", [_chunk("beta_doc", 0, "未发货退款政策(同题禁读)", tenant="beta")]
    )

    hits = index.search("未发货退款", top_k=1, filters=ChunkFilter(tenant="acme"))

    assert [hit.chunk.tenant for hit in hits] == ["acme"]
    assert len(hits) == 1


def test_replace_is_whole_group_swap_per_document() -> None:
    index = InMemoryKeywordIndex()
    index.replace("alpha", [_chunk("alpha", 0, "旧内容:年终奖发放时间")])
    index.replace("alpha", [_chunk("alpha", 0, "新内容:年假制度修订")])

    assert index.search("年终奖", top_k=5, filters=None) == []
    assert [hit.chunk.content for hit in index.search("年假制度", top_k=5, filters=None)] == [
        "新内容:年假制度修订"
    ]


def test_blank_or_foreign_query_returns_empty() -> None:
    index = InMemoryKeywordIndex()
    index.replace("alpha", [_chunk("alpha", 0, "年假制度")])

    assert index.search("", top_k=3, filters=None) == []
    assert index.search("养猫指南", top_k=3, filters=None) == []


def test_search_respects_top_k() -> None:
    index = InMemoryKeywordIndex()
    index.replace(
        "alpha",
        [
            _chunk("alpha", 0, "退款流程说明"),
            _chunk("alpha", 1, "退款 veterinarian 无关词:养老金计算"),
            _chunk("alpha", 2, "退款之外的其它条款"),
        ],
    )

    assert len(index.search("退款", top_k=2, filters=None)) == 2


def test_chunk_counts_misaligned_never_possible_but_empty_group_is_allowed() -> None:
    """空切片组 = 文档被清空:再次搜索不应返回该文档旧内容。"""
    index = InMemoryKeywordIndex()
    index.replace("alpha", [_chunk("alpha", 0, "退款政策")])
    index.replace("alpha", [])

    assert index.search("退款", top_k=3, filters=None) == []
