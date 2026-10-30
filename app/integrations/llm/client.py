"""统一 LLM 客户端:chat / embed / rerank 走同一套超时、重试、错误映射与用量日志。

领域代码只依赖本模块的数据结构与 AppError,不感知具体供应商;更换供应商只改配置。
真实上游只经 :class:`~app.integrations.llm.transport.HttpxTransport` 发起。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog

from app.core.config import Settings
from app.core.errors import (
    UpstreamAuthError,
    UpstreamBadResponseError,
    UpstreamError,
    UpstreamNotConfiguredError,
    UpstreamRateLimitedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from app.integrations.llm.transport import HttpxTransport, TransportResponse, UpstreamTransport

logger = structlog.get_logger("llm")

PROVIDER = "siliconflow"

SleepFn = Callable[[float], Awaitable[None]]
ClockFn = Callable[[], float]


def _parse_retry_after(headers: Mapping[str, str] | None) -> float | None:
    """解析 429 响应的 Retry-After 头:相对秒数或 HTTP 日期;解析不出返回 None。"""
    if not headers:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    raw = raw.strip()
    try:
        return max(float(raw), 0.0)
    except ValueError:
        pass
    try:
        due = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if due.tzinfo is None:
        return None
    return max((due - datetime.now(UTC)).total_seconds(), 0.0)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    model: str
    usage: TokenUsage
    latency_ms: float


@dataclass(frozen=True, slots=True)
class EmbedResult:
    vectors: list[list[float]]
    model: str
    usage: TokenUsage
    latency_ms: float


@dataclass(frozen=True, slots=True)
class RerankItem:
    index: int
    score: float


@dataclass(frozen=True, slots=True)
class RerankResult:
    items: list[RerankItem]
    model: str
    usage: TokenUsage
    latency_ms: float


@dataclass(slots=True)
class _CallOutcome:
    body: dict[str, Any]
    latency_ms: float
    attempts: int


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_openai_usage(raw: Any) -> TokenUsage:
    if not isinstance(raw, Mapping):
        return TokenUsage()
    prompt = _as_int(raw.get("prompt_tokens"))
    completion = _as_int(raw.get("completion_tokens"))
    total = _as_int(raw.get("total_tokens")) or prompt + completion
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


def _parse_rerank_usage(body: Mapping[str, Any]) -> TokenUsage:
    """兼容 Jina 风格 usage 与硅基流动 meta.tokens 两种用量字段。"""
    usage = _parse_openai_usage(body.get("usage"))
    if usage.total_tokens:
        return usage

    meta = body.get("meta")
    tokens = meta.get("tokens") if isinstance(meta, Mapping) else None
    if not isinstance(tokens, Mapping):
        return usage

    prompt = _as_int(tokens.get("input_tokens"))
    completion = _as_int(tokens.get("output_tokens"))
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


class UnifiedLLMClient:
    """唯一上游入口。所有失败都映射为 AppError 家族。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: UpstreamTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
        clock: ClockFn = time.monotonic,
    ) -> None:
        self._settings = settings
        self._transport = transport or HttpxTransport()
        self._sleep = sleep
        self._clock = clock
        self._throttle_lock = asyncio.Lock()
        # 相邻上游请求的最小间隔节拍(单调时刻);0 表示不限速
        self._next_send = 0.0

    async def aclose(self) -> None:
        close = getattr(self._transport, "aclose", None)
        if close is not None:
            await close()

    async def _throttle(self) -> None:
        """跨请求限速:相邻两次上游发送之间至少间隔 llm_min_interval_seconds。

        持锁串行取节拍:并发请求排队等各自的时隙,免费档配额不会被瞬间打爆。
        """
        interval = self._settings.llm_min_interval_seconds
        if interval <= 0:
            return
        async with self._throttle_lock:
            wait = self._next_send - self._clock()
            if wait > 0:
                logger.info(
                    "upstream_throttled",
                    wait_seconds=round(wait, 3),
                    interval=interval,
                )
                await self._sleep(wait)
            # 时隙从"上次发送时刻 + 间隔"起算;等待后偶尔略过节拍也按节拍左移
            self._next_send = max(self._clock(), self._next_send) + interval

    # ===== chat =====

    async def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ChatResult:
        model = model or self._settings.llm_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [dict(message) for message in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        outcome = await self._post(
            "/chat/completions",
            payload,
            operation="chat",
            model=model,
        )
        usage = _parse_openai_usage(outcome.body.get("usage"))
        content = self._extract_chat_content(outcome.body, model)
        self._log_success(operation="chat", model=model, usage=usage, outcome=outcome)
        return ChatResult(
            content=content,
            model=model,
            usage=usage,
            latency_ms=outcome.latency_ms,
        )

    def _extract_chat_content(self, body: Mapping[str, Any], model: str) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise self._bad_response("chat", model)
        first = choices[0]
        message = first.get("message") if isinstance(first, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise self._bad_response("chat", model)
        return content

    # ===== embed =====

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
    ) -> EmbedResult:
        model = model or self._settings.embedding_model
        payload = {"model": model, "input": list(texts)}
        outcome = await self._post("/embeddings", payload, operation="embed", model=model)
        usage = _parse_openai_usage(outcome.body.get("usage"))
        vectors = self._extract_vectors(outcome.body, model, expected_count=len(texts))
        self._log_success(operation="embed", model=model, usage=usage, outcome=outcome)
        return EmbedResult(
            vectors=vectors,
            model=model,
            usage=usage,
            latency_ms=outcome.latency_ms,
        )

    def _extract_vectors(
        self,
        body: Mapping[str, Any],
        model: str,
        *,
        expected_count: int,
    ) -> list[list[float]]:
        data = body.get("data")
        if not isinstance(data, list) or not data:
            raise self._bad_response("embed", model)
        try:
            ordered = sorted(data, key=lambda item: _as_int(item.get("index")))
            vectors = [[float(value) for value in item.get("embedding") or []] for item in ordered]
        except (AttributeError, TypeError, ValueError):
            raise self._bad_response("embed", model) from None
        # 数量必须与输入对齐,否则切片与向量会静默错位。
        if len(vectors) != expected_count or any(not vector for vector in vectors):
            raise self._bad_response("embed", model)
        return vectors

    # ===== rerank =====

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
    ) -> RerankResult:
        model = model or self._settings.rerank_model
        payload: dict[str, Any] = {
            "model": model,
            "query": query,
            "documents": list(documents),
        }
        if top_n is not None:
            payload["top_n"] = top_n

        outcome = await self._post("/rerank", payload, operation="rerank", model=model)
        usage = _parse_rerank_usage(outcome.body)
        items = self._extract_rerank_items(outcome.body, model)
        self._log_success(operation="rerank", model=model, usage=usage, outcome=outcome)
        return RerankResult(
            items=items,
            model=model,
            usage=usage,
            latency_ms=outcome.latency_ms,
        )

    def _extract_rerank_items(
        self,
        body: Mapping[str, Any],
        model: str,
    ) -> list[RerankItem]:
        results = body.get("results")
        if not isinstance(results, list):
            raise self._bad_response("rerank", model)
        try:
            return [
                RerankItem(
                    index=int(item["index"]),
                    score=float(item.get("relevance_score", item.get("score", 0.0))),
                )
                for item in results
            ]
        except (KeyError, TypeError, ValueError):
            raise self._bad_response("rerank", model) from None

    # ===== 统一请求管线 =====

    async def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        operation: str,
        model: str,
    ) -> _CallOutcome:
        api_key = self._settings.siliconflow_api_key
        if not api_key:
            raise UpstreamNotConfiguredError(
                provider=PROVIDER,
                operation=operation,
            )

        url = f"{self._settings.siliconflow_base_url.rstrip('/')}{path}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        max_attempts = 1 + self._settings.llm_max_retries
        started = time.perf_counter()

        for attempt in range(1, max_attempts + 1):
            error: UpstreamError
            retry_after: float | None = None
            try:
                await self._throttle()
                response = await self._transport.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self._settings.llm_timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                error = UpstreamTimeoutError(
                    provider=PROVIDER,
                    operation=operation,
                    details={"attempt": attempt, "cause": type(exc).__name__},
                )
            except httpx.TransportError as exc:
                error = UpstreamUnavailableError(
                    provider=PROVIDER,
                    operation=operation,
                    details={"attempt": attempt, "cause": type(exc).__name__},
                )
            else:
                if 200 <= response.status_code < 300:
                    if response.body is None:
                        logger.error(
                            "upstream_bad_response",
                            provider=PROVIDER,
                            operation=operation,
                            model=model,
                            status_code=response.status_code,
                            attempt=attempt,
                        )
                        raise UpstreamBadResponseError(
                            provider=PROVIDER,
                            operation=operation,
                            details={"status_code": response.status_code, "attempt": attempt},
                        )
                    return _CallOutcome(
                        body=response.body,
                        latency_ms=round((time.perf_counter() - started) * 1000, 2),
                        attempts=attempt,
                    )
                error = self._map_http_error(response, operation=operation, attempt=attempt)
                if response.status_code == 429:
                    retry_after = _parse_retry_after(response.headers)

            if not error.retryable or attempt >= max_attempts:
                logger.error(
                    "upstream_call_failed",
                    provider=PROVIDER,
                    operation=operation,
                    model=model,
                    code=error.code.value,
                    status_code=error.status_code,
                    attempt=attempt,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                )
                raise error

            # 429 带 Retry-After 时遵循服务端指示,否则按配置底数指数退避
            delay = (
                retry_after
                if retry_after is not None
                else self._settings.llm_retry_backoff_seconds * (2 ** (attempt - 1))
            )
            logger.warning(
                "upstream_call_retry",
                provider=PROVIDER,
                operation=operation,
                model=model,
                code=error.code.value,
                attempt=attempt,
                delay_seconds=round(delay, 3),
                retry_after=retry_after,
            )
            await self._sleep(delay)

        raise AssertionError("unreachable: retry loop must return or raise")  # pragma: no cover

    def _map_http_error(
        self,
        response: TransportResponse,
        *,
        operation: str,
        attempt: int,
    ) -> UpstreamError:
        status = response.status_code
        details = {"status_code": status, "attempt": attempt}
        if status == 429:
            return UpstreamRateLimitedError(provider=PROVIDER, operation=operation, details=details)
        if status >= 500:
            return UpstreamUnavailableError(provider=PROVIDER, operation=operation, details=details)
        if status in (401, 403):
            return UpstreamAuthError(provider=PROVIDER, operation=operation, details=details)
        return UpstreamBadResponseError(
            "上游模型服务拒绝了请求",
            provider=PROVIDER,
            operation=operation,
            details=details,
        )

    def _bad_response(self, operation: str, model: str) -> UpstreamBadResponseError:
        logger.error(
            "upstream_bad_response",
            provider=PROVIDER,
            operation=operation,
            model=model,
        )
        return UpstreamBadResponseError(provider=PROVIDER, operation=operation)

    def _log_success(
        self,
        *,
        operation: str,
        model: str,
        usage: TokenUsage,
        outcome: _CallOutcome,
    ) -> None:
        logger.info(
            "upstream_call",
            provider=PROVIDER,
            operation=operation,
            model=model,
            latency_ms=outcome.latency_ms,
            attempts=outcome.attempts,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )
