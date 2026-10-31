"""窄接缝:统一 LLM 客户端的重试退避策略(假传输层,不触网)。

与 test_llm_client.py 的分工:那里验证错误映射与请求契约,
这里验证退避节奏本身 —— 指数底数、抖动边界、Retry-After 优先与不重试条件。
"""

from __future__ import annotations

import random
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import UpstreamBadResponseError, UpstreamUnavailableError
from app.integrations.llm.client import UnifiedLLMClient
from app.integrations.llm.transport import TransportResponse

MESSAGES = [{"role": "user", "content": "年假怎么算?"}]

CHAT_OK_BODY: dict[str, Any] = {
    "choices": [{"message": {"role": "assistant", "content": "按工龄计算。"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
}


class FakeTransport:
    """替换系统边界(外部 HTTP):按预设队列依次返回响应或抛异常。"""

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


class SeededRandom(random.Random):
    """固定种子的随机源:抖动可复现,退避断言不 flaky。"""

    def __init__(self, seed: int = 1234) -> None:
        super().__init__(seed)


def build_client(
    settings: Settings,
    responses: list[TransportResponse | Exception],
    *,
    sleep: RecordingSleep,
    rng: SeededRandom | None = None,
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
    client = UnifiedLLMClient(
        configured,
        transport=transport,
        sleep=sleep,
        random_source=rng or SeededRandom(),
    )
    return client, transport


async def test_retry_backoff_grows_exponentially_with_bounded_jitter(settings):
    """退避按指数底数递增,并叠加有界抖动 —— 避免多客户端同刻重试。"""
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [
            TransportResponse(500, {}),
            TransportResponse(500, {}),
            TransportResponse(500, {}),
            TransportResponse(200, CHAT_OK_BODY),
        ],
        sleep=sleep,
        rng=SeededRandom(seed=7),
        llm_max_retries=3,
        llm_retry_backoff_seconds=0.5,
    )

    result = await client.chat(MESSAGES)

    assert result.content == "按工龄计算。"
    assert len(transport.calls) == 4
    assert len(sleep.delays) == 3
    # 底数:0.5 → 1.0 → 2.0;抖动不改变底数所在的量级区间
    for delay, base in zip(sleep.delays, [0.5, 1.0, 2.0], strict=True):
        assert base <= delay < base * 2


async def test_retry_backoff_is_deterministic_for_a_fixed_seed(settings):
    """同一随机源下退避可复现:离线评估与回归断言不 flaky。"""
    delays: list[list[float]] = []
    for _ in range(2):
        sleep = RecordingSleep()
        client, _ = build_client(
            settings,
            [
                TransportResponse(500, {}),
                TransportResponse(500, {}),
                TransportResponse(200, CHAT_OK_BODY),
            ],
            sleep=sleep,
            rng=SeededRandom(seed=42),
            llm_max_retries=2,
            llm_retry_backoff_seconds=0.5,
        )
        await client.chat(MESSAGES)
        delays.append(sleep.delays)

    assert delays[0] == delays[1]


async def test_retry_after_header_takes_precedence_over_backoff(settings):
    """上游明示 Retry-After 时按服务端指示等待,不做本地退避。"""
    sleep = RecordingSleep()
    client, _ = build_client(
        settings,
        [
            TransportResponse(429, {}, headers={"Retry-After": "2"}),
            TransportResponse(200, CHAT_OK_BODY),
        ],
        sleep=sleep,
        llm_max_retries=1,
        llm_retry_backoff_seconds=0.5,
    )

    await client.chat(MESSAGES)

    assert sleep.delays == [2.0]


async def test_non_retryable_client_error_is_not_retried(settings):
    """4xx 属客户端错误:不重试,直接上抛。"""
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


async def test_exhausted_retries_raise_last_error(settings):
    """重试次数用尽后上抛最后一次的错误,不多试。"""
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [TransportResponse(500, {}), TransportResponse(503, {})],
        sleep=sleep,
        llm_max_retries=1,
    )

    with pytest.raises(UpstreamUnavailableError):
        await client.chat(MESSAGES)

    assert len(transport.calls) == 2
    assert len(sleep.delays) == 1


async def test_timeout_is_retried(settings):
    """传输超时属可重试故障:重试后成功。"""
    sleep = RecordingSleep()
    client, transport = build_client(
        settings,
        [httpx.ReadTimeout("slow upstream"), TransportResponse(200, CHAT_OK_BODY)],
        sleep=sleep,
        llm_max_retries=1,
    )

    result = await client.chat(MESSAGES)

    assert result.content == "按工龄计算。"
    assert len(transport.calls) == 2
    assert len(sleep.delays) == 1
