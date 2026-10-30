"""检索服务主缝:高级检索管线各阶段与开关组合(不触网)。

验收口径:
- 全关 = T06 单向量基线时序(同一次 embed、同一次向量检索,分数逐字一致);
- 混合检索:两通道融合去重,同一切片不重复出现;
- 查询改写 / 多查询扩展:一次批量 embed,指向性可观察;
- MMR 在候选间求多样性,重排在 MMR 之前;
- 开关打开但协作对象缺失 = 装配错误,启动即失败。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.core.config import Settings
from app.domain.chunks import Chunk
from app.domain.retrieval import ChunkFilter, QueryPlan, ScoredChunk
from app.integrations.llm.client import EmbedResult, TokenUsage
from app.repositories.keyword_memory import InMemoryKeywordIndex
from app.repositories.vector_memory import InMemoryVectorStore
from app.services.retrieval import RetrievalService


def _chunk(document_id: str, index: int, content: str, *, tenant: str = "acme") -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}#{index}",
        document_id=document_id,
        index=index,
        content=content,
        tenant=tenant,
        version="v1",
        element_index_start=index,
        element_index_end=index + 1,
    )


class StubEmbedder:
    """按文本映射返回 canned 向量的 embed 替身,并记录每次调用。"""

    def __init__(self, vectors_by_text: dict[str, list[float]], dim: int = 3) -> None:
        self._mapping = vectors_by_text
        self._dim = dim
        self.embedded_batches: list[list[str]] = []

    async def embed(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> EmbedResult:
        self.embedded_batches.append(list(texts))
        vectors = [
            list(self._mapping.get(text, [0.0] * self._dim)) for text in texts
        ]
        return EmbedResult(
            vectors=vectors,
            model=model or "stub",
            usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            latency_ms=0.0,
        )


class FakeQueryPlanner:
    """脚本化查询规划替身:可控制改写与变体。"""

    def __init__(
        self,
        *,
        rewritten: str | None = None,
        variants: Sequence[str] = (),
    ) -> None:
        self._rewritten = rewritten
        self._variants = list(variants)
        self.calls: list[tuple[str, int]] = []

    async def plan(self, question: str, *, variant_count: int) -> QueryPlan:
        self.calls.append((question, variant_count))
        return QueryPlan(
            original=question, rewritten=self._rewritten, variants=list(self._variants)
        )


class FakeReranker:
    """脚本化重排替身:按内容长度颠倒序,分数按位置线性衰减。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    async def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, top_n: int
    ) -> list[ScoredChunk]:
        self.calls.append((query, len(candidates), top_n))
        ordered = sorted(candidates, key=lambda hit: len(hit.chunk.content), reverse=True)
        return [
            ScoredChunk(chunk=hit.chunk, score=round(1.0 - position * 0.1, 2))
            for position, hit in enumerate(ordered[:top_n])
        ]


def _settings(**updates: object) -> Settings:
    """管线分段测试的显式基线:全关;各用例再按需开启自己的开关。"""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": "test",
        "siliconflow_api_key": None,
        "use_query_rewrite": False,
        "use_multi_query": False,
        "use_hybrid": False,
        "use_mmr": False,
        "use_rerank": False,
    }
    settings_input = {**base, **updates}
    return Settings(**settings_input)  # type: ignore[arg-type]


def _seed_service(settings: Settings, **kwargs: object) -> RetrievalService:
    kwargs.setdefault("vector_store", InMemoryVectorStore())
    kwargs.setdefault("embedder", StubEmbedder({}))
    return RetrievalService(settings=settings, **kwargs)


# ===== 全关 = T06 基线时序 =====


async def test_all_toggles_off_reproduces_single_vector_search() -> None:
    """全关:一次 embed、一次向量检索,分数为手工可核查的余弦真值。"""
    store = InMemoryVectorStore()
    chunk_a = _chunk("doc", 0, "退款政策")
    chunk_b = _chunk("doc", 1, "年假制度")
    store.replace(
        "doc",
        [chunk_a, chunk_b],
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    )
    embedder = StubEmbedder({"押金退款": [0.6, 0.8, 0.0]})
    service = _seed_service(
        _settings(), vector_store=store, embedder=embedder
    )

    hits = await service.search("押金退款", top_k=2)

    assert embedder.embedded_batches == [["押金退款"]]
    assert [hit.chunk.chunk_id for hit in hits] == [chunk_b.chunk_id, chunk_a.chunk_id]
    assert hits[0].score == pytest.approx(0.8)
    assert hits[1].score == pytest.approx(0.6)


async def test_minmax_degenerate_channel_keeps_raw_scores() -> None:
    """单候选通道无区分度:按原分透传(不抬成满分,也不清零)。"""
    store = InMemoryVectorStore()
    chunk_a = _chunk("doc", 0, "唯一候选")
    store.replace("doc", [chunk_a], [[1.0, 0.0, 0.0]])
    keyword_index = InMemoryKeywordIndex()
    keyword_index.replace("doc", [chunk_a])
    embedder = StubEmbedder({"押金退款": [0.6, 0.8, 0.0]})
    service = _seed_service(
        _settings(use_hybrid=True, retrieval_vector_weight=1.0, retrieval_keyword_weight=0.0),
        vector_store=store,
        embedder=embedder,
        keyword_index=keyword_index,
    )

    hits = await service.search("押金退款", top_k=3)

    assert [hit.chunk.chunk_id for hit in hits] == [chunk_a.chunk_id]
    assert all(hit.score == pytest.approx(0.6) for hit in hits)


async def test_rerank_flag_without_collaborator_fails_fast() -> None:
    with pytest.raises(ValueError):
        _seed_service(_settings(use_rerank=True))


async def test_hybrid_flag_without_keyword_index_fails_fast() -> None:
    with pytest.raises(ValueError):
        _seed_service(_settings(use_hybrid=True))


async def test_rewrite_flag_without_planner_fails_fast() -> None:
    with pytest.raises(ValueError):
        _seed_service(_settings(use_query_rewrite=True))


# ===== 混合检索 =====


async def test_hybrid_fuses_channels_with_dedup() -> None:
    """向量通道只召回 A/B,关键词通道补回 C;A 双命中去重后仅出现一次。"""
    store = InMemoryVectorStore()
    chunk_a = _chunk("doc", 0, "退款流程说明")
    chunk_b = _chunk("doc", 1, "年假制度规定")
    chunk_c = _chunk("doc", 2, "押金退款细则")
    store.replace(
        "doc",
        [chunk_a, chunk_b, chunk_c],
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    )
    keyword_index = InMemoryKeywordIndex()
    keyword_index.replace("doc", [chunk_a, chunk_b, chunk_c])
    embedder = StubEmbedder({"押金退款": [0.9, 0.43588989, 0.0]})
    service = _seed_service(
        _settings(use_hybrid=True, retrieval_candidate_k=2),
        vector_store=store,
        embedder=embedder,
        keyword_index=keyword_index,
    )

    hits = await service.search("押金退款", top_k=2)

    ids = [hit.chunk.chunk_id for hit in hits]
    assert ids.count(chunk_a.chunk_id) == 1  # 双通道都命中 A:融合去重
    assert chunk_c.chunk_id in ids  # 关键词通道补回向量未召回的切片
    assert ids[0] == chunk_a.chunk_id
    assert [hit.score for hit in hits] == sorted(
        [hit.score for hit in hits], reverse=True
    )


async def test_hybrid_permission_filter_still_pre_retrieval() -> None:
    """混合通道的权限过滤都发生在打分前:禁读租户不出现、不占名额。"""
    store = InMemoryVectorStore()
    chunk_acme = _chunk("acme_doc", 0, "押金退款政策", tenant="acme")
    chunk_beta = _chunk("beta_doc", 0, "押金退款禁读", tenant="beta")
    store.replace(
        "acme_doc", [chunk_acme], [[1.0, 0.0, 0.0]],
    )
    store.replace("beta_doc", [chunk_beta], [[1.0, 0.0, 0.0]])
    keyword_index = InMemoryKeywordIndex()
    keyword_index.replace("acme_doc", [chunk_acme])
    keyword_index.replace("beta_doc", [chunk_beta])
    embedder = StubEmbedder({"押金退款": [1.0, 0.0, 0.0]})
    service = _seed_service(
        _settings(use_hybrid=True, retrieval_candidate_k=5),
        vector_store=store,
        embedder=embedder,
        keyword_index=keyword_index,
    )

    hits = await service.search("押金退款", top_k=1, filters=ChunkFilter(tenant="acme"))

    assert all(hit.chunk.tenant == "acme" for hit in hits)
    assert all(hit.chunk.chunk_id != chunk_beta.chunk_id for hit in hits)


# ===== 查询改写 / 多查询扩展 =====


async def test_query_rewrite_embeds_rewritten_query_single_batch() -> None:
    planner = FakeQueryPlanner(rewritten="未发货订单退款流程")
    embedder = StubEmbedder({"未发货订单退款流程": [1.0, 0.0, 0.0]})
    store = InMemoryVectorStore()
    chunk_a = _chunk("doc", 0, "退款政策")
    store.replace("doc", [chunk_a], [[1.0, 0.0, 0.0]])
    service = _seed_service(
        _settings(use_query_rewrite=True),
        vector_store=store,
        embedder=embedder,
        query_planner=planner,
    )

    hits = await service.search("没发货可以退钱吗", top_k=3)

    assert embedder.embedded_batches == [["未发货订单退款流程"]]
    assert [hit.chunk.chunk_id for hit in hits] == [chunk_a.chunk_id]


async def test_planner_returns_no_rewrite_falls_back_to_original_query() -> None:
    """规划器不可用(rewritten=None)时回退原查询:服务只消费 QueryPlan 契约。"""
    embedder = StubEmbedder({"原查询": [1.0, 0.0, 0.0]})
    store = InMemoryVectorStore()
    chunk_a = _chunk("doc", 0, "退款政策")
    store.replace("doc", [chunk_a], [[1.0, 0.0, 0.0]])
    service = _seed_service(
        _settings(use_query_rewrite=True),
        vector_store=store,
        embedder=embedder,
        query_planner=FakeQueryPlanner(),  # rewritten=None → 回退原查询
    )

    hits = await service.search("原查询", top_k=3)

    assert embedder.embedded_batches == [["原查询"]]
    assert [hit.chunk.chunk_id for hit in hits] == [chunk_a.chunk_id]


async def test_multi_query_expands_and_merges_with_dedup() -> None:
    """多查询扩展:一次批量 embed,多路向量检索结果融合去重。"""
    store = InMemoryVectorStore()
    chunk_a = _chunk("doc", 0, "主查询命中")
    chunk_x = _chunk("doc", 1, "变体才命中")
    store.replace("doc", [chunk_a, chunk_x], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    embedder = StubEmbedder(
        {
            "原始查询": [1.0, 0.0, 0.0],
            "变体一": [0.0, 1.0, 0.0],
            "变体二": [0.0, 0.0, 1.0],
        }
    )
    service = _seed_service(
        _settings(
            use_multi_query=True, retrieval_multi_query_count=2
        ),
        vector_store=store,
        embedder=embedder,
        query_planner=FakeQueryPlanner(variants=["变体一", "变体二"]),
    )

    hits = await service.search("原始查询", top_k=5)

    assert embedder.embedded_batches == [["原始查询", "变体一", "变体二"]]
    ids = [hit.chunk.chunk_id for hit in hits]
    assert set(ids) == {chunk_a.chunk_id, chunk_x.chunk_id}
    assert len(ids) == len(set(ids))


async def test_multi_query_only_does_not_leak_planner_rewrite() -> None:
    """开关独立:改写关闭时,一次 chat 产出的改写不得作为主查询生效。"""
    planner = FakeQueryPlanner(rewritten="未发货订单退款流程", variants=["变体一"])
    embedder = StubEmbedder(
        {
            "原查询": [1.0, 0.0, 0.0],
            "未发货订单退款流程": [0.0, 1.0, 0.0],
            "变体一": [0.0, 0.0, 1.0],
        }
    )
    store = InMemoryVectorStore()
    chunk_a = _chunk("doc", 0, "原查询才命中")
    chunk_b = _chunk("doc", 1, "改写后命中")
    store.replace("doc", [chunk_a, chunk_b], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    service = _seed_service(
        _settings(use_multi_query=True, retrieval_multi_query_count=1),
        vector_store=store,
        embedder=embedder,
        query_planner=planner,
    )

    hits = await service.search("原查询", top_k=5)

    # 改写没有生效:批量 embed 中只有「原查询 + 变体」,改写文本从未出现在任何通道
    assert embedder.embedded_batches == [["原查询", "变体一"]]
    assert all(
        "未发货订单退款流程" not in batch for batch in embedder.embedded_batches
    )
    ids = [hit.chunk.chunk_id for hit in hits]
    assert chunk_a.chunk_id in ids  # 变体一路检索正常融合


# ===== 重排 / MMR =====


async def test_rerank_orders_final_hits_and_truncates_to_top_k() -> None:
    store = InMemoryVectorStore()
    candidates = [_chunk("doc", i, f"第 {i} 个候选" + "长" * (2 - i)) for i in range(3)]
    store.replace(
        "doc",
        candidates,
        [[1.0, 0.0, 0.0], [0.9, 0.436, 0.0], [0.8, 0.6, 0.0]],
    )
    embedder = StubEmbedder({"查询": [1.0, 0.0, 0.0]})
    reranker = FakeReranker()
    service = _seed_service(
        _settings(use_rerank=True, retrieval_candidate_k=10),
        vector_store=store,
        embedder=embedder,
        reranker=reranker,
    )

    hits = await service.search("查询", top_k=2)

    # FakeReranker 按内容长度降序:内容最长的候选排前
    assert [(query, seen, top_n) for query, seen, top_n in reranker.calls] == [("查询", 3, 2)]
    assert [hit.chunk.chunk_id for hit in hits] == [
        candidates[0].chunk_id,
        candidates[1].chunk_id,
    ]
    assert hits[0].score == pytest.approx(1.0)
    assert [hit.score for hit in hits] == sorted(
        [hit.score for hit in hits], reverse=True
    )


async def test_mmr_replaces_duplicate_with_diverse_candidate() -> None:
    """纯相关序会把同一内容的重复切片都放进 top-k;MMR 打散补位多样性候选。"""
    store = InMemoryVectorStore()
    chunk_a = _chunk("doc", 0, "唯一者")
    chunk_dup = _chunk("doc", 1, "与 A 同向重复")
    chunk_diverse = _chunk("doc", 2, "正交方向")
    store.replace(
        "doc",
        [chunk_a, chunk_dup, chunk_diverse],
        [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    )
    embedder = StubEmbedder({"查询": [1.0, 0.0, 0.0]})
    service = _seed_service(
        _settings(use_mmr=True, retrieval_mmr_lambda=0.4),
        vector_store=store,
        embedder=embedder,
    )

    hits = await service.search("查询", top_k=2)

    assert [hit.chunk.chunk_id for hit in hits] == [
        chunk_a.chunk_id,
        chunk_diverse.chunk_id,
    ]


async def test_rerank_runs_before_mmr_when_both_on() -> None:
    store = InMemoryVectorStore()
    candidates = [
        _chunk("doc", 0, "AAAA"),  # 内容等长→按写入序反转
        _chunk("doc", 1, "BBBB"),
        _chunk("doc", 2, "CCCC"),
        _chunk("doc", 3, "DDDD"),
    ]
    store.replace(
        "doc",
        candidates,
        [[1.0, 0.0, 0.0], [0.9, 0.436, 0.0], [0.6, 0.8, 0.0], [0.0, 0.0, 1.0]],
    )
    embedder = StubEmbedder({"查询": [1.0, 0.0, 0.0]})
    reranker = FakeReranker()
    service = _seed_service(
        _settings(use_rerank=True, use_mmr=True, retrieval_candidate_k=4),
        vector_store=store,
        embedder=embedder,
        reranker=reranker,
    )

    hits = await service.search("查询", top_k=2)

    # 重排先跑,MMR 在重排产物上做多样性选择:候选池全量、top_n 保留全部候选
    query, seen, top_n = reranker.calls[0]
    assert query == "查询"
    assert seen == 4
    assert top_n == 4
    assert len(hits) == 2
