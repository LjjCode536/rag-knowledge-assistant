"""重排薄适配器:硅基流动 bge-reranker 经统一客户端的还原层(唯一自写轮子)。

只做三件事:候选内容报送统一客户端、把上游 index 还原成本地切片、
失败时按既定语义降级回退融合序。打分与重试都在统一 LLM 客户端,
本模块不新增网络逻辑。
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from app.core.errors import UpstreamError
from app.domain.retrieval import Reranker, ScoredChunk
from app.integrations.llm.client import UnifiedLLMClient

logger = structlog.get_logger("retrieval")


class SiliconFlowReranker(Reranker):
    """统一客户端 rerank 操作的薄适配,失败降级不中断检索。"""

    def __init__(self, llm_client: UnifiedLLMClient) -> None:
        self._client = llm_client

    async def rerank(
        self,
        query: str,
        candidates: Sequence[ScoredChunk],
        *,
        top_n: int,
    ) -> list[ScoredChunk]:
        """按重排相关性降序返回至多 top_n 个;上游失败回退融合序并留痕。"""
        if not candidates:
            return []

        ordered = sorted(candidates, key=lambda hit: hit.score, reverse=True)
        try:
            result = await self._client.rerank(
                query,
                [hit.chunk.content for hit in ordered],
                top_n=top_n,
            )
        except UpstreamError as exc:
            logger.warning(
                "retrieval_rerank_degraded",
                fallback="fusion_order",
                candidates=len(ordered),
                code=exc.code.value,
                status_code=exc.status_code,
                operation=exc.details.get("operation") if exc.details else None,
            )
            return ordered[:top_n]

        restored = [
            ScoredChunk(chunk=ordered[item.index].chunk, score=item.score)
            for item in result.items
        ]
        restored.sort(key=lambda hit: hit.score, reverse=True)
        return restored[:top_n]
