"""统一异常体系:稳定错误码 + HTTP 状态映射。

业务异常一律继承 :class:`AppError`;意外异常由异常处理器转换为
``INTERNAL_ERROR``,堆栈只进日志、不进响应。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """对调用方稳定的错误码。新增只追加,不改名。"""

    OK = "OK"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    BAD_REQUEST = "BAD_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"

    UPSTREAM_NOT_CONFIGURED = "UPSTREAM_NOT_CONFIGURED"
    UPSTREAM_AUTH_FAILED = "UPSTREAM_AUTH_FAILED"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_BAD_RESPONSE = "UPSTREAM_BAD_RESPONSE"


class AppError(Exception):
    """所有可预期异常的基类。"""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 500
    default_message: str = "服务内部错误"
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: ErrorCode | None = None,
        status_code: int | None = None,
    ) -> None:
        self.code = code or type(self).code
        self.status_code = status_code or type(self).status_code
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    status_code = 404
    default_message = "资源不存在"


class UpstreamError(AppError):
    """上游(模型服务)调用失败的基类。"""

    code = ErrorCode.UPSTREAM_UNAVAILABLE
    status_code = 502
    default_message = "上游服务调用失败"

    def __init__(
        self,
        message: str | None = None,
        *,
        provider: str = "siliconflow",
        operation: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {"provider": provider}
        if operation:
            merged["operation"] = operation
        if details:
            merged.update(details)
        super().__init__(message, details=merged)


class UpstreamNotConfiguredError(UpstreamError):
    code = ErrorCode.UPSTREAM_NOT_CONFIGURED
    status_code = 503
    default_message = "上游模型服务未配置,请设置 SILICONFLOW_API_KEY"


class UpstreamAuthError(UpstreamError):
    code = ErrorCode.UPSTREAM_AUTH_FAILED
    status_code = 502
    default_message = "上游模型服务鉴权失败"


class UpstreamRateLimitedError(UpstreamError):
    code = ErrorCode.UPSTREAM_RATE_LIMITED
    status_code = 429
    default_message = "上游模型服务限流,请稍后重试"
    retryable = True


class UpstreamTimeoutError(UpstreamError):
    code = ErrorCode.UPSTREAM_TIMEOUT
    status_code = 504
    default_message = "上游模型服务超时"
    retryable = True


class UpstreamUnavailableError(UpstreamError):
    code = ErrorCode.UPSTREAM_UNAVAILABLE
    status_code = 503
    default_message = "上游模型服务暂不可用"
    retryable = True


class UpstreamBadResponseError(UpstreamError):
    code = ErrorCode.UPSTREAM_BAD_RESPONSE
    status_code = 502
    default_message = "上游模型服务响应格式异常"
