"""统一 LLM 客户端:chat / embed / rerank 走同一套超时、重试、错误映射与用量日志。

领域代码只依赖本模块的数据结构与 AppError,不感知具体供应商;更换供应商只改配置。
真实上游只经 :class:`~app.integrations.llm.transport.HttpxTransport` 发起。

重试 / 退避 / Retry-After 等待 / 发送节拍由 tenacity 与 asyncio 节拍锁承接
(机制不自己造);错误家族与降级语义仍是本项目的领域契约。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
)

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


@dataclass(slots=True)
class _AttemptState:
    """一次重试循环的跨尝试上下文:当前尝试序号与最近一次 429 的 Retry-After。"""

    attempt: int = 0
    retry_after: float | None = None


class _CircuitBreaker:
    """上游熔断器:连续失败到阈值即打开,冷却期满放一次探测。

    三态:closed(正常)→ open(直接快速失败)→ half-open(放一次探测)→
    closed(探测成功)或 open(探测失败,重新计时)。

    机制简单自持:只数连续失败与冷却时间,不需要额外状态机库;
    上游硬挂时让请求快速失败,而不是每次都付满重试阶梯。
    """

    def __init__(self, *, failure_threshold: int, reset_seconds: float, clock: ClockFn) -> None:
        self._threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def enabled(self) -> bool:
        return self._threshold > 0

    @property
    def state(self) -> str:
        """熔断状态:closed / open / half-open(可观测)。"""
        if not self.enabled:
            return "closed"
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self._reset_seconds:
            return "half-open"
        return "open"

    def allows_request(self) -> bool:
        """是否放行本次请求:关闭态放行;半开态只放一次探测。"""
        if not self.enabled:
            return True
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at >= self._reset_seconds:
            # 半开:放一次探测(标记为已探测,探测失败则回到 open)
            self._opened_at = self._clock()
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self.enabled and self._failures >= self._threshold:
            self._opened_at = self._clock()

    def record_non_failure(self) -> None:
        """非故障结局(如客户端 4xx):不计入连续失败,也不改变熔断状态。"""


class _UsageModel(BaseModel):
    """上游 usage 字段:OpenAI 兼容口径(prompt / completion / total)。

    字段类型宽松:上游给脏数据时按 0 计,不让用量字段打挂问答主链路。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @field_validator("prompt_tokens", "completion_tokens", "total_tokens", mode="before")
    @classmethod
    def _lenient_int(cls, value: Any) -> int:
        return _coerce_int(value)

    @model_validator(mode="after")
    def _derive_total(self) -> _UsageModel:
        if not self.total_tokens:
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self

    def to_usage(self) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
        )


class _ChatContentPart(BaseModel):
    """chat content 的片段形态:部分供应商把 content 写成片段数组。"""

    text: str = ""


class _ChatMessage(BaseModel):
    role: str = ""
    content: str | list[_ChatContentPart] = ""


class _ChatChoice(BaseModel):
    message: _ChatMessage = Field(default_factory=_ChatMessage)


class _ChatCompletionResponse(BaseModel):
    choices: list[_ChatChoice] = Field(default_factory=list)
    usage: _UsageModel | None = None


class _EmbeddingDatum(BaseModel):
    index: int | None = None
    embedding: list[float] = Field(default_factory=list)


class _EmbeddingResponse(BaseModel):
    data: list[_EmbeddingDatum] = Field(default_factory=list)
    usage: _UsageModel | None = None


class _RerankDatum(BaseModel):
    index: int
    relevance_score: float | None = None
    score: float | None = None

    @property
    def effective_score(self) -> float:
        if self.relevance_score is not None:
            return self.relevance_score
        if self.score is not None:
            return self.score
        return 0.0


class _RerankResponse(BaseModel):
    # results 必填:缺字段即畸形响应(不能静默当成"无重排结果")
    results: list[_RerankDatum]
    usage: _UsageModel | None = None
    meta: dict[str, Any] | None = None


def _coerce_int(value: Any) -> int:
    """宽松取整数:上游给字符串或 None 时按 0 计,不打挂问答主链路。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _parse_openai_usage(raw: Any) -> TokenUsage:
    """OpenAI 兼容用量;脏数据按 0 计,total 缺失时按 prompt + completion 补齐。"""
    if not isinstance(raw, Mapping):
        return TokenUsage()
    try:
        return _UsageModel.model_validate(raw).to_usage()
    except ValidationError:
        return TokenUsage()


def _parse_rerank_usage(body: Mapping[str, Any]) -> TokenUsage:
    """兼容 Jina 风格 usage 与硅基流动 meta.tokens 两种用量字段。"""
    usage = _parse_openai_usage(body.get("usage"))
    if usage.total_tokens:
        return usage

    meta = body.get("meta")
    tokens = meta.get("tokens") if isinstance(meta, Mapping) else None
    if not isinstance(tokens, Mapping):
        return usage

    try:
        meta_usage = _UsageModel(
            prompt_tokens=_coerce_int(tokens.get("input_tokens")),
            completion_tokens=_coerce_int(tokens.get("output_tokens")),
        )
    except ValidationError:
        return usage
    return meta_usage.to_usage()


class UnifiedLLMClient:
    """唯一上游入口。所有失败都映射为 AppError 家族。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: UpstreamTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
        clock: ClockFn = time.monotonic,
        random_source: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or HttpxTransport()
        self._sleep = sleep
        self._clock = clock
        self._random = random_source or random.Random()
        self._throttle_lock = asyncio.Lock()
        # 相邻上游请求的最小间隔节拍(单调时刻);0 表示不限速
        self._next_send = 0.0
        self._breaker = _CircuitBreaker(
            failure_threshold=settings.llm_circuit_breaker_failures,
            reset_seconds=settings.llm_circuit_breaker_reset_seconds,
            clock=clock,
        )

    @property
    def circuit_breaker_state(self) -> str:
        """熔断状态(closed / open / half-open):供健康检查与日志留痕。"""
        return self._breaker.state

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
        try:
            parsed = _ChatCompletionResponse.model_validate(body)
        except ValidationError:
            raise self._bad_response("chat", model) from None
        if not parsed.choices:
            raise self._bad_response("chat", model)
        content = parsed.choices[0].message.content
        if isinstance(content, str):
            if not content:
                raise self._bad_response("chat", model)
            return content
        # 片段形态:按顺序拼接文本片段
        text = "".join(part.text for part in content)
        if not text:
            raise self._bad_response("chat", model)
        return text

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
        try:
            parsed = _EmbeddingResponse.model_validate(body)
        except ValidationError:
            raise self._bad_response("embed", model) from None
        if not parsed.data:
            raise self._bad_response("embed", model)
        # 按 index 排序;缺 index 的条目按出现顺序排在有 index 的条目之前
        ordered = sorted(
            enumerate(parsed.data),
            key=lambda pair: (
                pair[1].index is not None,
                pair[1].index if pair[1].index is not None else pair[0],
            ),
        )
        vectors = [datum.embedding for _, datum in ordered]
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
        try:
            parsed = _RerankResponse.model_validate(body)
        except ValidationError:
            raise self._bad_response("rerank", model) from None
        return [
            RerankItem(index=datum.index, score=datum.effective_score)
            for datum in parsed.results
        ]

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
        # 上一次尝试的上下文:Retry-After 只在紧邻的那次 429 上有意义
        attempt_state = _AttemptState()

        # 熔断打开:直接快速失败,不付重试阶梯的时间成本
        if not self._breaker.allows_request():
            logger.error(
                "upstream_circuit_open",
                provider=PROVIDER,
                operation=operation,
                model=model,
            )
            raise UpstreamUnavailableError(
                provider=PROVIDER,
                operation=operation,
                details={"reason": "circuit_open"},
            )

        try:
            return await self._attempt_with_retries(
                url,
                payload,
                headers=headers,
                operation=operation,
                model=model,
                max_attempts=max_attempts,
                started=started,
                attempt_state=attempt_state,
            )
        except BaseException as exc:
            # 熔断计数:可重试的上游故障计入连续失败,客户端错误不计
            self._record_breaker_outcome(exc)
            raise

    async def _attempt_with_retries(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        operation: str,
        model: str,
        max_attempts: int,
        started: float,
        attempt_state: _AttemptState,
    ) -> _CallOutcome:
        """重试循环本体:tenacity 承接退避与停止条件,领域错误在此映射。"""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=self._build_wait(attempt_state),
            retry=retry_if_exception(self._is_retryable_error),
            sleep=self._sleep,
            before_sleep=self._log_retry,
            reraise=True,
        ):
            with attempt:
                attempt_state.attempt = attempt.retry_state.attempt_number
                attempt_state.retry_after = None
                try:
                    await self._throttle()
                    response = await self._transport.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=self._settings.llm_timeout_seconds,
                    )
                except httpx.TimeoutException as exc:
                    raise self._map_transport_error(
                        exc,
                        operation=operation,
                        attempt=attempt_state.attempt,
                        timeout=True,
                    ) from exc
                except httpx.TransportError as exc:
                    raise self._map_transport_error(
                        exc,
                        operation=operation,
                        attempt=attempt_state.attempt,
                        timeout=False,
                    ) from exc

                if 200 <= response.status_code < 300:
                    if response.body is None:
                        logger.error(
                            "upstream_bad_response",
                            provider=PROVIDER,
                            operation=operation,
                            model=model,
                            status_code=response.status_code,
                            attempt=attempt_state.attempt,
                        )
                        raise UpstreamBadResponseError(
                            provider=PROVIDER,
                            operation=operation,
                            details={
                                "status_code": response.status_code,
                                "attempt": attempt_state.attempt,
                            },
                        )
                    self._breaker.record_success()
                    return _CallOutcome(
                        body=response.body,
                        latency_ms=round((time.perf_counter() - started) * 1000, 2),
                        attempts=attempt_state.attempt,
                    )
                if response.status_code == 429:
                    attempt_state.retry_after = _parse_retry_after(response.headers)
                raise self._map_http_error(
                    response,
                    operation=operation,
                    attempt=attempt_state.attempt,
                )

        raise AssertionError("unreachable: retry loop must return or raise")  # pragma: no cover

    def _build_wait(self, attempt_state: _AttemptState):
        """退避策略:指数底数 x 有界抖动;上游给了 Retry-After 则以服务端指示为准。

        抖动用注入的随机源,离线评估与回归断言可复现;生产避免多客户端
        在同一退避窗口同刻重试(惊群),对免费档配额尤其重要。
        """

        def wait(retry_state: RetryCallState) -> float:
            if attempt_state.retry_after is not None:
                return attempt_state.retry_after
            base = self._settings.llm_retry_backoff_seconds
            if base <= 0:
                return 0.0
            attempt = retry_state.attempt_number
            # 指数底数(第 1 次重试 = base)x 上界内抖动
            delay = base * (2 ** (attempt - 1))
            jitter = self._random.random() * base
            return min(delay + jitter, base * 2 ** max(self._settings.llm_max_retries, 0))

        return wait

    def _log_retry(self, retry_state: RetryCallState) -> None:
        """重试留痕:沿用既有 upstream_call_retry 事件字段,便于日志检索口径不变。"""
        outcome = retry_state.outcome
        error = outcome.exception() if outcome is not None else None
        if not isinstance(error, UpstreamError):
            return
        next_action = retry_state.next_action
        delay = round(next_action.sleep, 3) if next_action is not None else 0.0
        logger.warning(
            "upstream_call_retry",
            provider=PROVIDER,
            operation=error.details.get("operation") if error.details else None,
            code=error.code.value,
            attempt=retry_state.attempt_number,
            delay_seconds=delay,
        )

    def _record_breaker_outcome(self, error: BaseException | None) -> None:
        """熔断计数:可重试的上游故障计入连续失败;客户端错误不计。"""
        if error is None:
            self._breaker.record_success()
            return
        if isinstance(error, UpstreamError) and error.retryable:
            self._breaker.record_failure()
        else:
            self._breaker.record_non_failure()

    @staticmethod
    def _is_retryable_error(error: BaseException) -> bool:
        """只有标记为可重试的上游错误才进入下一次尝试。"""
        return isinstance(error, UpstreamError) and error.retryable

    def _map_transport_error(
        self,
        exc: Exception,
        *,
        operation: str,
        attempt: int,
        timeout: bool,
    ) -> UpstreamError:
        details = {"attempt": attempt, "cause": type(exc).__name__}
        if timeout:
            return UpstreamTimeoutError(
                provider=PROVIDER, operation=operation, details=details
            )
        return UpstreamUnavailableError(
            provider=PROVIDER, operation=operation, details=details
        )

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
