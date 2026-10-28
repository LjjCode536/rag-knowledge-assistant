"""统一响应信封:成功与失败共用同一形状。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.core.errors import AppError, ErrorCode


class ApiEnvelope[DataT](BaseModel):
    """code / message / data / details / trace_id / timestamp。"""

    code: ErrorCode = ErrorCode.OK
    message: str = "ok"
    data: DataT | None = None
    details: dict[str, Any] | None = None
    trace_id: str
    timestamp: datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def success_envelope[DataT](
    data: DataT | None,
    *,
    trace_id: str,
    message: str = "ok",
) -> ApiEnvelope[DataT]:
    return ApiEnvelope[DataT](
        code=ErrorCode.OK,
        message=message,
        data=data,
        trace_id=trace_id,
        timestamp=utcnow(),
    )


def error_envelope(error: AppError, *, trace_id: str) -> dict[str, Any]:
    return ApiEnvelope[None](
        code=error.code,
        message=error.message,
        data=None,
        details=error.details,
        trace_id=trace_id,
        timestamp=utcnow(),
    ).model_dump(mode="json")
