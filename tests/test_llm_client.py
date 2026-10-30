"""窄接缝:统一 LLM 客户端的错误映射与用量解析(假传输层,不触网)。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import (
    ErrorCode,
    UpstreamAuthError,
    UpstreamBadResponseError,
    UpstreamNotConfiguredError,
    UpstreamRateLimitedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from app.core.logging import configure_logging
from app.integrations.llm.client import UnifiedLLMClient
from app.integrations.llm.transport import TransportResponse
from tests.support import json_log_entries

MESSAGES = [{"role": "user", "content": "年假怎么算?"}]

CHAT_OK_BODY: dict[str, Any] = {
    "id": "chatcmpl-1",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "按工龄计算。"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
}


class FakeTransport:
    """替换系统边界(外部 HTTP),记录调用以便断言请求契约。"""

    def __init__(self, responses: list[TransportResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> TransportResponse:
        self.calls.append({"url": url, "json": json, "headers": dict(headers), "timeout": timeout})
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class FakeClock:
    """可编程单调时钟:sleep 推进自身,等待与节拍计算完全确定。"""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.delays: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.now += seconds


def build_client(
    settings: Settings,
    responses: list[TransportResponse | Exception],
    *,
    sleep: RecordingSleep | None = None,
    clock=None,
    **overrides: Any,
) -> tuple[UnifiedLLMClient, FakeTransport]:
    values: dict[str, Any] = {
        "siliconflow_api_key": "sk-test-placeholder",
        "llm_max_retries": 0,
        "llm_retry_backoff_seconds": 0.5,
    }
    values.update(overrides)
    configured = settings.model_copy(update=values)
    transport = FakeTransport(responses)
    kwargs: dict[str, Any] = {"sleep": sleep or RecordingSleep()}
    if clock is not None:
        kwargs["clock"] = clock
    client = UnifiedLLMClient(configured, transport=transport, **kwargs)
    return client, transport


async def test_chat_sends_openai_compatible_request_and_parses_usage(settings):
    client, transport = build_client(settings, [TransportResponse(200, CHAT_OK_BODY)])

    result = await client.chat(MESSAGES)

    assert result.content == "按工龄计算。"
    assert result.model == "Qwen/Qwen3-8B"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (11, 7)
    assert result.usage.total_tokens == 18
    assert result.latency_ms >= 0

    call = transport.calls[0]
    assert call["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-test-placeholder"
    assert call["json"]["model"] == "Qwen/Qwen3-8B"
    assert call["json"]["messages"] == MESSAGES
    assert call["timeout"] == settings.llm_timeout_seconds


async def test_embed_orders_vectors_by_index_and_parses_usage(settings):
    body = {
        "data": [
            {"index": 1, "embedding": [0.2, 0.3]},
            {"index": 0, "embedding": [0.1]},
        ],
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }
    client, transport = build_client(settings, [TransportResponse(200, body)])

    result = await client.embed(["甲", "乙"])

    assert result.vectors == [[0.1], [0.2, 0.3]]
    assert result.model == "BAAI/bge-m3"
    assert result.usage.total_tokens == 5
    assert transport.calls[0]["url"].endswith("/embeddings")
    assert transport.calls[0]["json"]["input"] == ["甲", "乙"]


async def test_embed_count_mismatch_maps_to_bad_response(settings):
    body = {
        "data": [{"index": 0, "embedding": [0.1]}],
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }
    client, _ = build_client(settings, [TransportResponse(200, body)])

    with pytest.raises(UpstreamBadResponseError):
        await client.embed(["甲", "乙"])


async def test_rerank_parses_scores_and_meta_usage(settings):
    body = {
        "id": "rerank-1",
        "results": [
            {"index": 2, "relevance_score": 0.91},
            {"index": 0, "relevance_score": 0.4},
        ],
        "meta": {"tokens": {"input_tokens": 12, "output_tokens": 3}},
    }
    client, transport = build_client(settings, [TransportResponse(200, body)])

    result = await client.rerank("年假怎么算?", ["甲", "乙", "丙"])

    assert [(item.index, item.score) for item in result.items] == [(2, 0.91), (0, 0.4)]
    assert result.usage.prompt_tokens == 12
    assert result.usage.completion_tokens == 3
    assert result.usage.total_tokens == 15
    call = transport.calls[0]
    assert call["url"].endswith("/rerank")
    assert call["json"]["model"] == "BAAI/bge-reranker-v2-m3"
    assert call["json"]["documents"] == ["甲", "乙", "丙"]


async def test_missing_api_key_fails_fast_with_not_configured(settings):
    unconfigured = settings.model_copy(update={"siliconflow_api_key": None})
    transport = FakeTransport([])
    client = UnifiedLLMClient(unconfigured, transport=transport)

    with pytest.raises(UpstreamNotConfiguredError) as exc_info:
        await client.chat(MESSAGES)

    error = exc_info.value
    assert error.code is ErrorCode.UPSTREAM_NOT_CONFIGURED
    assert error.status_code == 503
    assert error.details == {"provider": "siliconflow", "operation": "chat"}
    assert transport.calls == []


@pytest.mark.parametrize(
    ("failure", "expected_error", "expected_status"),
    [
        (httpx.ReadTimeout("slow upstream"), UpstreamTimeoutError, 504),
        (httpx.ConnectError("dns down"), UpstreamUnavailableError, 503),
        (TransportResponse(429, {"message": "rate limited"}), UpstreamRateLimitedError, 429),
        (TransportResponse(500, {"message": "boom"}), UpstreamUnavailableError, 503),
        (TransportResponse(503, None), UpstreamUnavailableError, 503),
        (TransportResponse(401, {}), UpstreamAuthError, 502),
        (TransportResponse(400, {"message": "bad input"}), UpstreamBadResponseError, 502),
    ],
)
async def test_chat_maps_upstream_failures_to_app_errors(
    settings, failure, expected_error, expected_status
):
    client, _ = build_client(settings, [failure])

    with pytest.raises(expected_error) as exc_info:
        await client.chat(MESSAGES)

    assert exc_info.value.status_code == expected_status
    assert exc_info.value.details["operation"] == "chat"
    assert exc_info.value.details["provider"] == "siliconflow"


async def test_malformed_success_body_maps_to_bad_response(settings):
    client, _ = build_client(settings, [TransportResponse(200, {"choices": []})])

    with pytest.raises(UpstreamBadResponseError) as exc_info:
        await client.chat(MESSAGES)

    assert exc_info.value.code is ErrorCode.UPSTREAM_BAD_RESPONSE


async def _call_operation(client: UnifiedLLMClient, operation: str):
    if operation == "chat":
        return await client.chat(MESSAGES)
    if operation == "embed":
        return await client.embed(["连通性检查"])
    return await client.rerank("年假怎么算?", ["年假规定", "报销流程"])


@pytest.mark.parametrize("operation", ["chat", "embed", "rerank"])
@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (httpx.ReadTimeout("slow upstream"), UpstreamTimeoutError),
        (TransportResponse(429, {}), UpstreamRateLimitedError),
        (TransportResponse(500, {}), UpstreamUnavailableError),
    ],
)
async def test_all_three_operations_share_error_mapping(
    settings, operation, failure, expected_error
):
    client, _ = build_client(settings, [failure])

    with pytest.raises(expected_error) as exc_info:
        await _call_operation(client, operation)

    assert exc_info.value.details["operation"] == operation


async def test_rate_limit_is_retried_then_succeeds(settings):
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [TransportResponse(429, {}), TransportResponse(200, CHAT_OK_BODY)],
        sleep=sleep,
        llm_max_retries=1,
    )

    result = await client.chat(MESSAGES)

    assert result.content == "按工龄计算。"
    assert len(transport.calls) == 2
    assert sleep.delays == [0.5]


async def test_exhausted_retries_raise_last_error(settings):
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [TransportResponse(500, {}), TransportResponse(502, {})],
        sleep=sleep,
        llm_max_retries=1,
    )

    with pytest.raises(UpstreamUnavailableError):
        await client.chat(MESSAGES)

    assert len(transport.calls) == 2
    assert sleep.delays == [0.5]


async def test_client_error_is_not_retried(settings):
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [TransportResponse(400, {}), TransportResponse(200, CHAT_OK_BODY)],
        sleep=sleep,
        llm_max_retries=2,
    )

    with pytest.raises(UpstreamBadResponseError):
        await client.chat(MESSAGES)

    assert len(transport.calls) == 1
    assert sleep.delays == []


async def test_success_logs_usage_and_latency(settings, capsys):
    configure_logging(settings)
    client, _ = build_client(settings, [TransportResponse(200, CHAT_OK_BODY)])

    await client.chat(MESSAGES)

    entries = json_log_entries(capsys.readouterr().out)
    calls = [entry for entry in entries if entry.get("event") == "upstream_call"]

    assert calls
    logged = calls[-1]
    assert logged["operation"] == "chat"
    assert logged["model"] == "Qwen/Qwen3-8B"
    assert logged["prompt_tokens"] == 11
    assert logged["completion_tokens"] == 7
    assert logged["total_tokens"] == 18
    assert logged["latency_ms"] >= 0
    assert logged["attempts"] == 1


async def test_throttle_spaces_consecutive_calls(settings):
    clock = FakeClock()
    client, transport = build_client(
        settings,
        [TransportResponse(200, CHAT_OK_BODY), TransportResponse(200, CHAT_OK_BODY)],
        sleep=clock.sleep,
        clock=clock.time,
        llm_min_interval_seconds=0.5,
    )

    await client.chat(MESSAGES)
    await client.chat(MESSAGES)

    assert len(transport.calls) == 2
    # 首请求不动等待;次请求被压到节拍上,恰好等了一个最小间隔
    assert clock.delays == [0.5]
    assert clock.now == pytest.approx(0.5)


async def test_throttle_disabled_by_default_never_waits(settings):
    clock = FakeClock()
    client, transport = build_client(
        settings,
        [TransportResponse(200, CHAT_OK_BODY), TransportResponse(200, CHAT_OK_BODY)],
        sleep=clock.sleep,
        clock=clock.time,
    )

    await client.chat(MESSAGES)
    await client.chat(MESSAGES)

    assert len(transport.calls) == 2
    assert clock.delays == []


async def test_429_honors_retry_after_seconds(settings):
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [
            TransportResponse(429, {}, headers={"Retry-After": "2"}),
            TransportResponse(200, CHAT_OK_BODY),
        ],
        sleep=sleep,
        llm_max_retries=1,
    )

    result = await client.chat(MESSAGES)

    assert result.content == "按工龄计算。"
    assert len(transport.calls) == 2
    # 上游明示 2 秒再试:退避遵循服务端指示,而不是固定指数基数
    assert sleep.delays == [2.0]


async def test_429_honors_retry_after_http_date(settings):
    due = datetime.now(UTC) + timedelta(seconds=60)
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [
            TransportResponse(429, {}, headers={"Retry-After": format_datetime(due, usegmt=True)}),
            TransportResponse(200, CHAT_OK_BODY),
        ],
        sleep=sleep,
        llm_max_retries=1,
    )

    await client.chat(MESSAGES)

    assert len(transport.calls) == 2
    assert 55.0 <= sleep.delays[0] <= 60.0


async def test_429_without_retry_after_keeps_exponential_backoff(settings):
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [
            TransportResponse(429, {}),
            TransportResponse(429, {}, headers={"Retry-After": "garbage"}),
            TransportResponse(200, CHAT_OK_BODY),
        ],
        sleep=sleep,
        llm_max_retries=2,
    )

    await client.chat(MESSAGES)

    assert len(transport.calls) == 3
    assert sleep.delays == [0.5, 1.0]
