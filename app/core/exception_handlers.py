"""把业务异常、校验异常、意外异常统一收敛为响应信封。"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.envelope import error_envelope
from app.core.errors import AppError, ErrorCode
from app.core.tracing import TRACE_ID_HEADER, trace_id_of

logger = structlog.get_logger("errors")

_HTTP_ERROR_MAP: dict[int, tuple[ErrorCode, str]] = {
    400: (ErrorCode.BAD_REQUEST, "请求参数不合法"),
    404: (ErrorCode.NOT_FOUND, "接口不存在"),
    405: (ErrorCode.METHOD_NOT_ALLOWED, "请求方法不允许"),
    422: (ErrorCode.VALIDATION_ERROR, "请求校验失败"),
}


def _error_response(request: Request, error: AppError) -> JSONResponse:
    trace_id = trace_id_of(request)
    return JSONResponse(
        status_code=error.status_code,
        content=error_envelope(error, trace_id=trace_id),
        headers={TRACE_ID_HEADER: trace_id},
    )


def _http_error_to_app_error(exc: StarletteHTTPException) -> AppError:
    code, message = _HTTP_ERROR_MAP.get(
        exc.status_code,
        (
            ErrorCode.BAD_REQUEST if exc.status_code < 500 else ErrorCode.INTERNAL_ERROR,
            "请求失败",
        ),
    )
    return AppError(
        message,
        code=code,
        status_code=exc.status_code,
        details={"status_code": exc.status_code},
    )


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留定位所需字段,避免把原始输入回显给调用方。"""
    sanitized: list[dict[str, Any]] = []
    for item in errors:
        sanitized.append(
            {
                "loc": [str(part) for part in item.get("loc", ())],
                "type": item.get("type", "unknown"),
                "message": item.get("msg", ""),
            }
        )
    return sanitized


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "app_error",
            trace_id=trace_id_of(request),
            code=exc.code.value,
            status_code=exc.status_code,
            error_message=exc.message,
            details=exc.details,
        )
        return _error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        error = AppError(
            "请求校验失败",
            code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            details={"errors": _sanitize_validation_errors(list(exc.errors()))},
        )
        logger.warning(
            "validation_error",
            trace_id=trace_id_of(request),
            details=error.details,
        )
        return _error_response(request, error)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        error = _http_error_to_app_error(exc)
        logger.warning(
            "http_error",
            trace_id=trace_id_of(request),
            code=error.code.value,
            status_code=error.status_code,
            error_message=error.message,
        )
        return _error_response(request, error)

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 堆栈只进日志;响应只暴露稳定错误码与通用文案。
        logger.error(
            "unhandled_exception",
            trace_id=trace_id_of(request),
            exc_info=exc,
            error_type=type(exc).__name__,
        )
        return _error_response(request, AppError())
