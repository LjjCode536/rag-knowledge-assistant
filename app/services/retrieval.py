"""检索编排:查询向量化 → 检索前过滤 → top-k 相似度检索。

查询与切片走同一个向量模型与同一个 Embedder 接缝;向量库内
过滤发生在相似度计算之前(见 ADR-0002→0003 的过滤契约)。
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.domain.retrieval import ChunkFilter, ScoredChunk, VectorStore
from app.services.embedding import Embedder

logger = structlog.get_logger("retrieval")


class RetrievalService:
    """把用户问题变成向量,在向量库中检索前过滤后取 top-k。"""

    def __init__(self, *, vector_store: VectorStore, embedder: Embedder) -> None:
        self._vectors = vector_store
        self._embedder = embedder

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        filters: ChunkFilter | None = None,
    ) -> list[ScoredChunk]:
        """相似度检索一次:返回按分数降序的切片,附完整切片元数据。"""
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
