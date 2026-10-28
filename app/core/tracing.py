"""全链路 trace_id:透传或生成 `X-Request-ID`,绑定到日志与错误响应。"""

from __future__ import annotations

import re
import time
import uuid
from contextvars import ContextVar

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TRACE_ID_HEADER = "X-Request-ID"

_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def resolve_trace_id(incoming: str | None) -> str:
    """复用调用方 trace_id;格式不合法时重新生成,避免日志注入。"""
    if incoming and _TRACE_ID_PATTERN.fullmatch(incoming.strip()):
        return incoming.strip()
    return new_trace_id()


def current_trace_id() -> str | None:
    return _trace_id_var.get()


class TraceIdMiddleware:
    """为每个 HTTP 请求绑定 trace_id,并写回响应头。

    响应头由本中间件写入;发生未捕获异常时响应由最外层
    ``ServerErrorMiddleware`` 直接返回,因此异常处理器也会显式写回同一头。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        trace_id = resolve_trace_id(headers.get(TRACE_ID_HEADER))
        scope.setdefault("state", {})["trace_id"] = trace_id

        token = _trace_id_var.set(trace_id)
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        logger = structlog.get_logger("http")
        started = time.perf_counter()
        status_code = 500

        async def send_with_trace(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[TRACE_ID_HEADER] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            logger.info(
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            structlog.contextvars.clear_contextvars()
            _trace_id_var.reset(token)


def trace_id_of(request: Request) -> str:
    """从请求状态读取 trace_id,缺失时兜底生成。"""
    trace_id = getattr(request.state, "trace_id", None)
    if isinstance(trace_id, str) and trace_id:
        return trace_id
    return current_trace_id() or new_trace_id()
