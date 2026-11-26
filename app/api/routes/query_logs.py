"""查询日志端点:原始问题、命中、拒答原因与延迟可查(T05 / M3 基线下半)。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.api.dependencies import require_principal
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.errors import NotFoundError
from app.core.tracing import trace_id_of
from app.domain.identity import Principal, Role
from app.domain.qa import (
    GenerationUsage,
    GeneratorKind,
    QueryHit,
    QueryLog,
    RefusalReason,
)
from app.domain.repositories import QueryLogRepository

router = APIRouter(prefix="/api/v1/query-logs", tags=["qa"])


def _visible_principal_id(principal: Principal) -> str | None:
    """读路径主体过滤:成员只看自己的,管理员看本租户全部。"""
    return None if principal.role is Role.ADMIN else principal.id


class QueryHitView(BaseModel):
    """命中视图:排名、切片定位、分数与是否进入回答证据。"""

    rank: int
    chunk_id: str
    document_id: str
    title: str | None = None
    page_number: int | None = None
    score: float
    used: bool

    @classmethod
    def of(cls, hit: QueryHit) -> QueryHitView:
        return cls(**hit.model_dump())


class QueryLogView(BaseModel):
    """查询日志视图:问题、答案、拒答原因、命中、Token 用量与端到端延迟。"""

    query_id: str
    trace_id: str
    tenant: str
    principal_id: str
    question: str
    answer: str
    refused: bool
    refusal_reason: RefusalReason | None = None
    hits: list[QueryHitView]
    filters: dict[str, str]
    top_k: int
    min_score: float
    latency_ms: float
    generator: GeneratorKind
    token_usage: GenerationUsage
    rewritten_question: str | None = None
    expanded_questions: list[str]
    created_at: datetime

    @classmethod
    def of(cls, log: QueryLog) -> QueryLogView:
        return cls(
            query_id=log.query_id,
            trace_id=log.trace_id,
            tenant=log.tenant,
            principal_id=log.principal_id,
            question=log.question,
            answer=log.answer,
            refused=log.refused,
            refusal_reason=log.refusal_reason,
            hits=[QueryHitView.of(hit) for hit in log.hits],
            filters=log.filters,
            top_k=log.top_k,
            min_score=log.min_score,
            latency_ms=log.latency_ms,
            generator=log.generator,
            token_usage=log.token_usage,
            rewritten_question=log.rewritten_question,
            expanded_questions=log.expanded_questions,
            created_at=log.created_at,
        )


class QueryLogsView(BaseModel):
    """查询日志列表:总数 + 当前页条数。"""

    total: int
    limit: int
    items: list[QueryLogView]


@router.get("", response_model=ApiEnvelope[QueryLogsView], summary="查询日志(最近优先)")
def list_query_logs(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
    limit: int = Query(default=20, ge=1, le=100),
) -> ApiEnvelope[QueryLogsView]:
    """按登录主体返回查询日志:成员只看自己的,管理员看本租户全部。"""
    repository: QueryLogRepository = request.app.state.query_log_repository
    principal_id = _visible_principal_id(principal)
    logs = repository.list_recent(
        tenant=principal.tenant, limit=limit, principal_id=principal_id
    )
    data = QueryLogsView(
        total=repository.count(tenant=principal.tenant, principal_id=principal_id),
        limit=limit,
        items=[QueryLogView.of(log) for log in logs],
    )
    return success_envelope(data, trace_id=trace_id_of(request))


@router.get(
    "/{query_id}",
    response_model=ApiEnvelope[QueryLogView],
    summary="按 query_id 查询日志",
)
def get_query_log(
    request: Request,
    query_id: str,
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApiEnvelope[QueryLogView]:
    repository: QueryLogRepository = request.app.state.query_log_repository
    log = repository.get(
        query_id,
        tenant=principal.tenant,
        principal_id=_visible_principal_id(principal),
    )
    if log is None:
        raise NotFoundError("查询日志不存在", details={"query_id": query_id})
    return success_envelope(QueryLogView.of(log), trace_id=trace_id_of(request))
