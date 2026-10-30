"""检索编排:M4 高级检索管线(教材第 43 章生产替换清单)。

改写 → 多查询扩展 → 混合检索(向量 + 关键词通道,融合去重)→ 重排
→ MMR;各能力经配置逐项开关。全部关闭时退回 T06 单向量基线时序:
一次 embed、一次检索前过滤的 top-k 相似度检索,分数逐字一致。

权限过滤始终发生在打分之前(ADR-0002→0003 的过滤契约),两条通道都遵守。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from app.core.config import Settings
from app.domain.chunks import Chunk
from app.domain.retrieval import (
    ChunkFilter,
    KeywordIndex,
    QueryPlan,
    QueryPlanner,
    Reranker,
    ScoredChunk,
    VectorStore,
)
from app.services.embedding import Embedder

logger = structlog.get_logger("retrieval")


@dataclass(slots=True)
class _FusionEntry:
    """融合累加:单切片在双通道下的加权分与固定分母权重。"""

    chunk: Chunk
    weighted: float = 0.0
    weights: float = 0.0


def _minmax(scores: list[float]) -> list[float]:
    """通道内归一:适合融合的 [0,1] 区间;分数没有区分度时按原分透传。

    无区分度(如单候选、同分)时归一无法提供任何信息:既不抬成满分,
    也不清零,而是保留通道内相对幅度(cos 类通道可继续对照拒答阈值)。
    """
    lowest = min(scores)
    span = max(scores) - lowest
    if span <= 0.0:
        return list(scores)
    return [(score - lowest) / span for score in scores]


def _cosine_between(
    a: list[float] | None, b: list[float] | None
) -> float:
    """M4 阶段多样性计算的余弦;向量缺失按 0 处理,不作惩罚也不作奖励。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class RetrievalService:
    """把用户问题变成候选集:规划、双通道召回、融合、重排与多样性精选。"""

    def __init__(
        self,
        *,
        vector_store: VectorStore,
        embedder: Embedder,
        settings: Settings,
        keyword_index: KeywordIndex | None = None,
        query_planner: QueryPlanner | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self._vectors = vector_store
        self._embedder = embedder
        self._settings = settings
        self._keyword_index = keyword_index
        self._planner = query_planner
        self._reranker = reranker
        self._check_wiring()

    def _check_wiring(self) -> None:
        """开关打开而协作对象缺失是装配错误:启动即失败,不带病运行。"""
        missing: list[str] = []
        if self._settings.use_hybrid and self._keyword_index is None:
            missing.append("keyword_index (use_hybrid)")
        if (self._settings.use_query_rewrite or self._settings.use_multi_query) and (
            self._planner is None
        ):
            missing.append("query_planner (use_query_rewrite / use_multi_query)")
        if self._settings.use_rerank and self._reranker is None:
            missing.append("reranker (use_rerank)")
        if missing:
            raise ValueError("高级检索开关开启但缺少协作对象:" + "; ".join(missing))

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        filters: ChunkFilter | None = None,
    ) -> list[ScoredChunk]:
        """返回按最终分数降序的切片,附完整切片元数据。"""
        started = datetime.now(UTC)
        settings = self._settings
        if not (
            settings.use_query_rewrite
            or settings.use_multi_query
            or settings.use_hybrid
            or settings.use_mmr
            or settings.use_rerank
        ):
            return await self._legacy_search(query, top_k=top_k, filters=filters)

        hits = await self._advanced_search(query, top_k=top_k, filters=filters)
        self._log_completed(query, top_k, filters, hits, started)
        return hits

    # ===== 基线路径(全关,分数与时序与 T06 完全一致)=====

    async def _legacy_search(
        self,
        query: str,
        *,
        top_k: int,
        filters: ChunkFilter | None,
    ) -> list[ScoredChunk]:
        started = datetime.now(UTC)
        embedded_query = await self._embedder.embed([query])
        hits = self._vectors.search(
            embedded_query.vectors[0], top_k=top_k, filters=filters
        )
        effective_filters = (filters or ChunkFilter()).effective()
        logger.info(
            "retrieval_completed",
            query_chars=len(query),
            top_k=top_k,
            filters=effective_filters,
            hits=len(hits),
            best_score=hits[0].score if hits else None,
            embed_latency_ms=embedded_query.latency_ms,
            duration_ms=round(
                (datetime.now(UTC) - started).total_seconds() * 1000, 2
            ),
        )
        return hits

    # ===== 高级路径 =====

    async def _advanced_search(
        self,
        query: str,
        *,
        top_k: int,
        filters: ChunkFilter | None,
    ) -> list[ScoredChunk]:
        settings = self._settings
        plan = QueryPlan(original=query)
        if settings.use_query_rewrite or settings.use_multi_query:
            assert self._planner is not None
            plan = await self._planner.plan(
                query, variant_count=settings.retrieval_multi_query_count
            )

        # 开关可用性独立:改写只在 use_query_rewrite 打开时消费,
        # 多查询关闭时变体不参与 embed——一次 chat 产出不等于一次全量生效。
        primary = (plan.rewritten if settings.use_query_rewrite else None) or plan.original
        queries = [primary]
        if settings.use_multi_query:
            queries.extend(plan.variants)
        embedded_queries = await self._embedder.embed(queries)

        candidate_k = max(settings.retrieval_candidate_k, top_k)
        candidates = self._fuse_channels(
            embedded_queries.vectors,
            primary_query=primary,
            filters=filters,
            candidate_k=candidate_k,
        )

        if settings.use_rerank:
            assert self._reranker is not None
            rerank_top_n = candidate_k if settings.use_mmr else top_k
            candidates = await self._reranker.rerank(
                primary, candidates, top_n=rerank_top_n
            )

        if settings.use_mmr:
            candidates = self._mmr_select(
                embedded_queries.vectors[0],
                candidates,
                top_k=top_k,
                lmbda=settings.retrieval_mmr_lambda,
            )
        else:
            candidates = candidates[:top_k]
        return candidates

    def _fuse_channels(
        self,
        query_vectors: list[list[float]],
        *,
        primary_query: str,
        filters: ChunkFilter | None,
        candidate_k: int,
    ) -> list[ScoredChunk]:
        """双通道召回 + 归一融合去重;通道各自维持检索前过滤契约。

        向量通道:多查询变体逐路检索,同一切片取各路最好分;
        关键词通道:BM25 打分的候选,只有混合检索打开时才参与。
        融合分 = 各通道 min-max 归一分按权重的加权平均(缺通道按 0 分)。
        """
        settings = self._settings

        vector_hits: dict[str, ScoredChunk] = {}
        for query_vector in query_vectors:
            for hit in self._vectors.search(
                query_vector, top_k=candidate_k, filters=filters
            ):
                best = vector_hits.get(hit.chunk.chunk_id)
                if best is None or hit.score > best.score:
                    vector_hits[hit.chunk.chunk_id] = hit

        vector_weight = settings.retrieval_vector_weight
        keyword_weight = settings.retrieval_keyword_weight if settings.use_hybrid else 0.0
        active_weight_total = vector_weight + keyword_weight

        fused: dict[str, _FusionEntry] = {}
        if vector_weight > 0.0 and vector_hits:
            ordered = sorted(vector_hits.values(), key=lambda hit: hit.score, reverse=True)
            vector_ns = _minmax([hit.score for hit in ordered])
            for hit, ns in zip(ordered, vector_ns, strict=True):
                fused[hit.chunk.chunk_id] = _FusionEntry(
                    chunk=hit.chunk,
                    weighted=vector_weight * ns,
                    weights=active_weight_total,
                )

        if keyword_weight > 0.0 and self._keyword_index is not None:
            keyword_hits = self._keyword_index.search(
                primary_query, top_k=candidate_k, filters=filters
            )
            if keyword_hits:
                keyword_ns = _minmax([hit.score for hit in keyword_hits])
                for hit, ns in zip(keyword_hits, keyword_ns, strict=True):
                    entry = fused.get(hit.chunk.chunk_id)
                    if entry is None:
                        # 缺失通道按 0 分计入:分母恒为双通道权重和,权重稳定可比。
                        entry = _FusionEntry(
                            chunk=hit.chunk, weights=active_weight_total
                        )
                        fused[hit.chunk.chunk_id] = entry
                    entry.weighted += keyword_weight * ns
                    entry.weights = active_weight_total

        candidates = [
            ScoredChunk(
                chunk=entry.chunk,
                score=(entry.weighted / entry.weights) if entry.weights > 0.0 else 0.0,
            )
            for entry in fused.values()
        ]
        candidates.sort(key=lambda hit: hit.score, reverse=True)
        return candidates

    def _mmr_select(
        self,
        query_vector: list[float],
        candidates: list[ScoredChunk],
        *,
        top_k: int,
        lmbda: float,
    ) -> list[ScoredChunk]:
        """最大边缘相关:重排 / 融合分作相关性项,候选间相似度作多样性惩罚。"""
        selected: list[ScoredChunk] = []
        remaining = list(candidates)
        candidate_vectors: dict[str, list[float] | None] = {}
        selected_vectors: list[list[float] | None] = []
        while remaining and len(selected) < top_k:
            best_hit = None
            best_score = -math.inf
            for hit in remaining:
                chunk_vector = candidate_vectors.setdefault(
                    hit.chunk.chunk_id,
                    self._vectors.vector_of(hit.chunk.chunk_id),
                )
                diversity_penalty = max(
                    (
                        _cosine_between(chunk_vector, selected_vector)
                        for selected_vector in selected_vectors
                    ),
                    default=0.0,
                )
                mmr = lmbda * hit.score - (1 - lmbda) * diversity_penalty
                if mmr > best_score:
                    best_score, best_hit = mmr, hit
            assert best_hit is not None
            selected.append(best_hit)
            remaining.remove(best_hit)
            selected_vectors.append(candidate_vectors[best_hit.chunk.chunk_id])
        return selected

    def _log_completed(
        self,
        query: str,
        top_k: int,
        filters: ChunkFilter | None,
        hits: list[ScoredChunk],
        started: datetime,
    ) -> None:
        settings = self._settings
        logger.info(
            "retrieval_completed",
            query_chars=len(query),
            top_k=top_k,
            filters=(filters or ChunkFilter()).effective(),
            hits=len(hits),
            best_score=hits[0].score if hits else None,
            embed_latency_ms=None,
            duration_ms=round((datetime.now(UTC) - started).total_seconds() * 1000, 2),
            pipeline="advanced",
            query_rewrite_used=settings.use_query_rewrite,
            multi_query_used=settings.use_multi_query,
            hybrid_used=settings.use_hybrid,
            rerank_used=settings.use_rerank,
            mmr_used=settings.use_mmr,
        )
