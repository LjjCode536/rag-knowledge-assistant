"""健康检查端点:只留存活与粗粒度依赖状态(ADR-0020)。

缓存命中率、上游熔断与向量身份盘点属于诊断内部状态,已移到管理员端点
``/api/v1/admin/diagnostics``;公开面不再暴露这些细节。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import Settings
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of

router = APIRouter(tags=["system"])


class HealthData(BaseModel):
    """公共健康视图:存活、版本、环境与上游是否已配置(不细述上游状态)。"""

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
