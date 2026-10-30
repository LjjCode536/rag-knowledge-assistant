"""重排薄适配器缝:SiliconFlowReranker(全项目唯一自写轮子,不触网)。

验收口径:
- 上游 index 还原成本地候选顺序,score 换成重排分;
- 上游失败(超时 / 限流 / 不可用)按已配好的错误映射落到日志,降级回退
  融合序继续检索,绝不因增强能力挂掉(用户决策:降级不中断);
- 空候选不发起上游调用。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.logging import configure_logging
from app.domain.chunks import Chunk
from app.domain.retrieval import ScoredChunk
from app.integrations.llm.client import UnifiedLLMClient
from app.integrations.llm.transport import TransportResponse
from app.services.reranking import SiliconFlowReranker
from tests.support import json_log_entries
from tests.test_llm_client import FakeTransport


def _candidate(index: int, content: str, score: float) -> ScoredChunk:
    chunk = Chunk(
        chunk_id=f"c{index}",
        document_id="d",
        index=index,
        content=content,
        tenant="acme",
        version="v1",
        element_index_start=index,
        element_index_end=index + 1,
    )
    return ScoredChunk(chunk=chunk, score=score)


def _keyed_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"siliconflow_api_key": "k-test", "llm_max_retries": 0}
    )


def _client_with_transport(
    settings: Settings, responses: list[TransportResponse | Exception]
):
    transport = FakeTransport(responses)
    client = UnifiedLLMClient(settings, transport=transport)
    return SiliconFlowReranker(client), transport


RERANK_OK_BODY: dict[str, Any] = {
    "id": "rerank-1",
    "results": [
        {"index": 2, "relevance_score": 0.91},
        {"index": 0, "relevance_score": 0.42},
    ],
    "meta": {"tokens": {"input_tokens": 12, "output_tokens": 3}},
}


async def test_rerank_restores_candidates_and_replaces_scores(settings) -> None:
    responses = [TransportResponse(200, RERANK_OK_BODY)]
    reranker, _ = _client_with_transport(_keyed_settings(settings), responses)
    candidates = [
        _candidate(0, "未发货退款政策", 0.5),
        _candidate(1, "无关年假政策", 0.4),
        _candidate(2, "退款发票流程", 0.3),
    ]

    hits = await reranker.rerank("退款怎么办", candidates, top_n=2)

    assert [hit.chunk.chunk_id for hit in hits] == ["c2", "c0"]
    assert hits[0].score == pytest.approx(0.91)
    assert hits[1].score == pytest.approx(0.42)


async def test_rerank_keeps_chunk_metadata_intact(settings) -> None:
    responses = [TransportResponse(200, RERANK_OK_BODY)]
    reranker, _ = _client_with_transport(_keyed_settings(settings), responses)
    candidates = [
        _candidate(0, "未发货退款政策", 0.5),
        _candidate(1, "无关年假政策", 0.4),
        _candidate(2, "退款发票流程", 0.3),
    ]

    hits = await reranker.rerank("退款怎么办", candidates, top_n=2)

    assert hits[0].chunk.content == "退款发票流程"
    assert hits[0].chunk.tenant == "acme"


async def test_empty_candidates_skips_upstream(settings) -> None:
    reranker, transport = _client_with_transport(_keyed_settings(settings), [])

    hits = await reranker.rerank("退款怎么办", [], top_n=2)

    assert hits == []
    assert transport.calls == []


@pytest.mark.parametrize(
    "upstream_failure",
    [
        httpx.ReadTimeout("slow reranker"),
        httpx.ConnectError("dns down"),
        TransportResponse(429, {"message": "rate limited"}),
        TransportResponse(500, {"message": "boom"}),
        TransportResponse(401, {}),
        TransportResponse(200, {"results": {"index": "broken"}}),
    ],
)
async def test_upstream_failure_degrades_to_fusion_order(
    settings, capsys, upstream_failure
) -> None:
    """上游任何失败都映射后降级:回退原分数序,检索不中断。"""
    keyed = _keyed_settings(settings)
    configure_logging(keyed)
    reranker, transport = _client_with_transport(_keyed_settings(settings), [upstream_failure])
    candidates = [
        _candidate(0, "第一候选", 0.9),
        _candidate(1, "第二候选", 0.8),
        _candidate(2, "第三候选", 0.7),
    ]

    hits = await reranker.rerank("退款怎么办", candidates, top_n=2)

    assert [hit.chunk.chunk_id for hit in hits] == ["c0", "c1"]
    assert [hit.score for hit in hits] == [0.9, 0.8]
    assert len(transport.calls) == 1  # llm_max_retries=0:失败不重试
    entries = json_log_entries(capsys.readouterr().out)
    degraded = [
        entry
        for entry in entries
        if entry.get("event") == "retrieval_rerank_degraded"
    ]
    assert len(degraded) == 1
    assert degraded[0]["fallback"] == "fusion_order"
    assert degraded[0]["candidates"] == 3


async def test_rerank_sends_documents_in_candidate_order(settings) -> None:
    responses = [TransportResponse(200, RERANK_OK_BODY)]
    reranker, transport = _client_with_transport(_keyed_settings(settings), responses)
    candidates = [
        _candidate(0, "未发货退款政策", 0.5),
        _candidate(1, "无关年假政策", 0.4),
        _candidate(2, "退款发票流程", 0.3),
    ]

    await reranker.rerank("退款怎么办", candidates, top_n=2)

    call = transport.calls[0]
    assert call["url"].endswith("/rerank")
    assert call["json"]["documents"] == ["未发货退款政策", "无关年假政策", "退款发票流程"]
    assert call["json"]["top_n"] == 2
    assert call["json"]["model"] == "BAAI/bge-reranker-v2-m3"
