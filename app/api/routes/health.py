"""健康检查端点,附在线问答缓存的命中率统计(T15)。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import Settings
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of
from app.services.qa_cache import AnswerCache

router = APIRouter(tags=["system"])


class QaCacheStats(BaseModel):
    """问答答档缓存的命中率统计:累计命中 / 未命中与命中率。"""

    enabled: bool
    hits: int
    misses: int
    hit_rate: float | None = None


class HealthData(BaseModel):
    status: str
    version: str
    environment: str
    upstream_configured: bool
    qa_cache: QaCacheStats


def _qa_cache_stats(request: Request) -> QaCacheStats:
    """禁用模式下 app.state.answer_cache 为 None(装配显式关闭),返回关闭形态。"""
    cache: AnswerCache | None = request.app.state.answer_cache
    if cache is None:
        return QaCacheStats(enabled=False, hits=0, misses=0, hit_rate=None)
    stats = cache.stats()
    return QaCacheStats(
        enabled=True, hits=stats.hits, misses=stats.misses, hit_rate=stats.hit_rate
    )


@router.get("/health", response_model=ApiEnvelope[HealthData], summary="健康检查")
async def health(request: Request) -> ApiEnvelope[HealthData]:
    settings: Settings = request.app.state.settings
    data = HealthData(
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
        upstream_configured=settings.upstream_configured,
        qa_cache=_qa_cache_stats(request),
    )
    return success_envelope(data, trace_id=trace_id_of(request))
