"""窄接缝:统一 LLM 客户端的熔断(假传输层,不触网)。

上游硬挂(连续 5xx / 连接失败)时,每次请求都付满 3x30s 的重试阶梯
= 90s 才失败。熔断让硬挂的上游快速失败:打开状态下直接拒绝,
半开状态放一次探测请求,成功即恢复。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import UpstreamUnavailableError
from app.integrations.llm.client import UnifiedLLMClient
from app.integrations.llm.transport import TransportResponse

MESSAGES = [{"role": "user", "content": "年假怎么算?"}]

CHAT_OK_BODY: dict[str, Any] = {
    "choices": [{"message": {"role": "assistant", "content": "按工龄计算。"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


class FakeTransport:
    """替换系统边界(外部 HTTP):按预设队列依次返回响应。"""

    def __init__(self, responses: list[TransportResponse]) -> None:
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
        return self._responses.pop(0)


class RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def build_client(
    settings: Settings,
    responses: list[TransportResponse],
    *,
    sleep: RecordingSleep | None = None,
    **overrides: Any,
) -> tuple[UnifiedLLMClient, FakeTransport]:
    values: dict[str, Any] = {
        "siliconflow_api_key": "sk-test-placeholder",
        "llm_max_retries": 0,
        "llm_retry_backoff_seconds": 0.5,
        "llm_circuit_breaker_failures": 3,
        "llm_circuit_breaker_reset_seconds": 60.0,
    }
    values.update(overrides)
    configured = settings.model_copy(update=values)
    transport = FakeTransport(responses)
    client = UnifiedLLMClient(
        configured, transport=transport, sleep=sleep or RecordingSleep()
    )
    return client, transport


async def test_breaker_opens_after_repeated_failures(settings):
    """连续失败达到阈值:熔断打开,后续请求直接快速失败。"""
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [TransportResponse(500, {}) for _ in range(10)],
        sleep=sleep,
        llm_circuit_breaker_failures=3,
    )

    for _ in range(3):
        with pytest.raises(UpstreamUnavailableError):
            await client.chat(MESSAGES)

    calls_before = len(transport.calls)
    with pytest.raises(UpstreamUnavailableError):
        await client.chat(MESSAGES)

    # 熔断打开后不再打上游:直接失败
    assert len(transport.calls) == calls_before


async def test_breaker_reset_allows_probe_after_cooldown(settings):
    """冷却期后放一次探测请求:上游恢复即回到正常态。"""
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [TransportResponse(500, {}) for _ in range(3)] + [TransportResponse(200, CHAT_OK_BODY)],
        sleep=sleep,
        llm_circuit_breaker_failures=3,
        llm_circuit_breaker_reset_seconds=0.0,
    )

    for _ in range(3):
        with pytest.raises(UpstreamUnavailableError):
            await client.chat(MESSAGES)

    result = await client.chat(MESSAGES)

    assert result.content == "按工龄计算。"
    assert len(transport.calls) == 4


async def test_breaker_stays_closed_on_success(settings):
    """成功请求不计入失败阈值:熔断不误开。"""
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [TransportResponse(200, CHAT_OK_BODY) for _ in range(10)],
        sleep=sleep,
        llm_circuit_breaker_failures=3,
    )

    for _ in range(5):
        result = await client.chat(MESSAGES)
        assert result.content == "按工龄计算。"

    assert len(transport.calls) == 5


async def test_breaker_disabled_by_zero_threshold(settings):
    """阈值为 0:熔断关闭,行为与原来一致。"""
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [TransportResponse(500, {}) for _ in range(5)],
        sleep=sleep,
        llm_circuit_breaker_failures=0,
    )

    for _ in range(5):
        with pytest.raises(UpstreamUnavailableError):
            await client.chat(MESSAGES)

    assert len(transport.calls) == 5


async def test_breaker_state_is_observable(settings):
    """熔断状态可观测:供 /health 与日志留痕。"""
    sleep = RecordingSleep()
    client, _ = build_client(
        settings,
        [TransportResponse(500, {}) for _ in range(5)],
        sleep=sleep,
        llm_circuit_breaker_failures=2,
    )

    assert client.circuit_breaker_state == "closed"

    for _ in range(2):
        with pytest.raises(UpstreamUnavailableError):
            await client.chat(MESSAGES)

    assert client.circuit_breaker_state == "open"
