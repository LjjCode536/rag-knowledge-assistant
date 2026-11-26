"""管理端点:审计事件只读查询与诊断信息(ADR-0020)。

两类端点都只对 ``admin`` 与 ``platform_operator`` 开放:
- 租户 ``admin`` 的租户来自 token,携带 ``tenant`` 参数即 403;
- ``platform_operator`` 必须显式给出 ``tenant``,每次成功访问写审计。

``/health`` 保持公开且只留存活与粗粒度依赖;缓存命中率、向量身份盘点等
诊断信息移到这里,不再暴露给匿名调用方。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.api.dependencies import require_admin_tenant
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.errors import BadRequestError
from app.core.tracing import trace_id_of
from app.domain.audit import AuditAction, AuditEvent, AuditResult
from app.domain.retrieval import EmbeddingRef
from app.domain.stats import as_utc
from app.services.audit import AuditService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
# 兼容简短的只读路径:同样只对管理员开放,不额外暴露能力
alias_router = APIRouter(prefix="/api/v1", tags=["admin"])


class AuditEventView(BaseModel):
    """一条审计事件视图:时间 / 租户 / 主体 / 动作 / 资源 / 结果 / trace_id / 细节。"""

    event_id: str
    occurred_at: datetime
    tenant: str
    principal_id: str | None = None
    action: AuditAction
    resource_type: str | None = None
    resource_id: str | None = None
    result: AuditResult
    trace_id: str | None = None
    details: dict[str, Any]

    @classmethod
    def of(cls, event: AuditEvent) -> AuditEventView:
        return cls(**event.model_dump())


class AuditEventListView(BaseModel):
    """审计事件清单:符合筛选条件的总数 + 当前页。"""

    total: int
    limit: int
    offset: int
    items: list[AuditEventView]


class QaCacheStatsView(BaseModel):
    """诊断用缓存命中率:只对管理员可见。"""

    enabled: bool
    hits: int
    misses: int
    hit_rate: float | None = None


class IdentityCountView(BaseModel):
    """按来源身份盘点向量数量;身份未知(历史行)三项为空。"""

    model: str | None = None
    version: str | None = None
    dimension: int | None = None
    count: int


class DiagnosticsView(BaseModel):
    """系统诊断:缓存、上游熔断与向量身份;租户由管理端点显式解析。"""

    tenant: str
    vector_store: str
    embedding: dict[str, Any]
    identity_counts: list[IdentityCountView]
    qa_cache: QaCacheStatsView
    upstream: dict[str, str]


def _resolve_range(
    start: datetime | None, end: datetime | None
) -> tuple[datetime | None, datetime | None]:
    start = as_utc(start) if start is not None else None
    end = as_utc(end) if end is not None else None
    if start is not None and end is not None and start >= end:
        raise BadRequestError(
            "时间区间起点必须早于终点", details={"start": start, "end": end}
        )
    return start, end


@router.get(
    "/audit-events",
    response_model=ApiEnvelope[AuditEventListView],
    summary="审计事件(管理员;按动作与时间过滤)",
)
@alias_router.get(
    "/audit-events",
    response_model=ApiEnvelope[AuditEventListView],
    include_in_schema=False,
)
@alias_router.get(
    "/audit-logs",
    response_model=ApiEnvelope[AuditEventListView],
    include_in_schema=False,
)
@alias_router.get(
    "/audits",
    response_model=ApiEnvelope[AuditEventListView],
    include_in_schema=False,
)
def list_audit_events(
    request: Request,
    tenant: Annotated[str, Depends(require_admin_tenant)],
    action: Annotated[
        AuditAction | None, Query(description="按动作精确匹配")
    ] = None,
    start: Annotated[datetime | None, Query(description="区间起点(含)")] = None,
    end: Annotated[datetime | None, Query(description="区间终点(不含)")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApiEnvelope[AuditEventListView]:
    """按租户、动作与时间倒序列出审计事件;平台管理员跨租户访问逐次留痕。"""
    start, end = _resolve_range(start, end)
    service: AuditService = request.app.state.audit_service
    events, total = service.list_events(
        tenant=tenant,
        actions=[action] if action is not None else None,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    data = AuditEventListView(
        total=total,
        limit=limit,
        offset=offset,
        items=[AuditEventView.of(event) for event in events],
    )
    return success_envelope(data, trace_id=trace_id_of(request))


@router.get(
    "/diagnostics",
    response_model=ApiEnvelope[DiagnosticsView],
    summary="系统诊断(管理员;缓存 / 上游 / 向量身份)",
)
@alias_router.get(
    "/diagnostics",
    response_model=ApiEnvelope[DiagnosticsView],
    include_in_schema=False,
)
def get_diagnostics(
    request: Request,
    tenant: Annotated[str, Depends(require_admin_tenant)],
) -> ApiEnvelope[DiagnosticsView]:
    """诊断信息不再出现在公开 ``/health``;平台管理员访问同样逐次审计。"""
    settings = request.app.state.settings
    vector_store = request.app.state.vector_store
    cache = request.app.state.answer_cache
    client = getattr(request.app.state, "llm_client", None)
    identity = EmbeddingRef(
        model=settings.embedding_model,
        version=settings.embedding_version,
        dimension=settings.embedding_dimension,
    )
    counts: list[IdentityCountView] = []
    try:
        for ref, count in vector_store.identity_counts().items():
            counts.append(
                IdentityCountView(
                    model=ref.model if ref is not None else None,
                    version=ref.version if ref is not None else None,
                    dimension=ref.dimension if ref is not None else None,
                    count=count,
                )
            )
    except Exception:
        # 盘点失败不阻断诊断端点:仍返回其余可用信息
        counts = []
    qa_cache = (
        QaCacheStatsView(
            enabled=True,
            hits=cache.stats().hits,
            misses=cache.stats().misses,
            hit_rate=cache.stats().hit_rate,
        )
        if cache is not None
        else QaCacheStatsView(enabled=False, hits=0, misses=0, hit_rate=None)
    )
    data = DiagnosticsView(
        tenant=tenant,
        vector_store=settings.vector_store,
        embedding={
            "model": identity.model,
            "version": identity.version,
            "dimension": identity.dimension,
        },
        identity_counts=counts,
        qa_cache=qa_cache,
        upstream={
            "circuit_breaker": getattr(client, "circuit_breaker_state", "closed")
        },
    )
    return success_envelope(data, trace_id=trace_id_of(request))
