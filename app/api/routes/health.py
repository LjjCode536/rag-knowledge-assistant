"""健康检查端点。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import Settings
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of

router = APIRouter(tags=["system"])


class HealthData(BaseModel):
    status: str
    version: str
    environment: str
    upstream_configured: bool


@router.get("/health", response_model=ApiEnvelope[HealthData], summary="健康检查")
async def health(request: Request) -> ApiEnvelope[HealthData]:
    settings: Settings = request.app.state.settings
    data = HealthData(
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
        upstream_configured=settings.upstream_configured,
    )
    return success_envelope(data, trace_id=trace_id_of(request))
