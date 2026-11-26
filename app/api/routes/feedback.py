"""反馈端点:有用 / 无用反馈落库可查,差评自动汇总为黄金用例候选(T11)。"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import require_principal
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.errors import NotFoundError
from app.core.tracing import trace_id_of
from app.domain.feedback import (
    FeedbackOrigin,
    FeedbackRecord,
    FeedbackVerdict,
    GoldenCaseCandidate,
)
from app.domain.identity import Principal, Role
from app.domain.qa import RefusalReason
from app.services.feedback import FeedbackService

router = APIRouter(prefix="/api/v1/feedback", tags=["qa"])


def _visible_principal_id(principal: Principal) -> str | None:
    """读路径主体过滤:成员只看自己的,管理员看本租户全部。"""
    return None if principal.role is Role.ADMIN else principal.id

_CANDIDATE_CSV_CONTENT_TYPE = "text/csv; charset=utf-8"
_CANDIDATE_CSV_HEADER = (
    "id,question,occurrences,failure,first_seen,last_seen,comments,origin_query_ids"
)


class FeedbackIn(BaseModel):
    """反馈提交:指向一条问答的 query_id + 判定 + 可选一句话说明。"""

    query_id: str = Field(min_length=1)
    verdict: FeedbackVerdict
    comment: str | None = Field(default=None, max_length=200)

    @field_validator("comment")
    @classmethod
    def _blank_comment_is_none(cls, value: str | None) -> str | None:
        """空白说明按未填处理,便于前端把空输入直接透传。"""
        if value is not None and not value.strip():
            return None
        return value


class FeedbackView(BaseModel):
    """反馈视图:feedback_id + 与查询日志同源的 trace_id + 判定与说明。"""

    feedback_id: str
    query_id: str
    trace_id: str
    tenant: str
    principal_id: str
    verdict: FeedbackVerdict
    comment: str | None = None
    created_at: datetime

    @classmethod
    def of(cls, record: FeedbackRecord) -> FeedbackView:
        return cls(**record.model_dump())


class FeedbackViewList(BaseModel):
    """反馈列表:总数 + 当前页条数。"""

    total: int
    limit: int
    items: list[FeedbackView]


class FeedbackOriginView(BaseModel):
    """差评来源视图:差评落在哪次问答上,答案当时长什么样。"""

    query_id: str
    trace_id: str
    verdict: FeedbackVerdict
    refused: bool
    refusal_reason: RefusalReason | None = None
    top_documents: list[str]
    comment: str | None = None
    created_at: datetime

    @classmethod
    def of(cls, origin: FeedbackOrigin) -> FeedbackOriginView:
        return cls(**origin.model_dump())


class GoldenCaseCandidateView(BaseModel):
    """黄金用例候选视图:同问题差评聚类出的待改进项,可直接人工整理入黄金数据集。"""

    id: str
    question: str
    occurrences: int
    failure: str
    first_seen: datetime
    last_seen: datetime
    comments: list[str]
    origins: list[FeedbackOriginView]

    @classmethod
    def of(cls, candidate: GoldenCaseCandidate) -> GoldenCaseCandidateView:
        return cls(
            **{**candidate.model_dump(), "origins": [
                FeedbackOriginView.of(origin) for origin in candidate.origins
            ]}
        )


class NegativeCandidatesView(BaseModel):
    """差评候选清单:``total`` 是差评反馈总条数,同问题差评聚成一组(items)。"""

    total: int
    limit: int
    items: list[GoldenCaseCandidateView]


@router.post(
    "",
    response_model=ApiEnvelope[FeedbackView],
    summary="提交回答反馈(有用 / 无用,可附一句话说明)",
)
def submit_feedback(
    request: Request,
    body: FeedbackIn,
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApiEnvelope[FeedbackView]:
    """反馈经 query_id 关联查询日志并复制其 trace_id;同一问答重复提交按现有反馈覆盖。

    反馈只能挂在自己的问答上:成员与管理员用他人 query_id 提交都返回 404。
    """
    service: FeedbackService = request.app.state.feedback_service
    record = service.submit(
        body.query_id,
        body.verdict,
        comment=body.comment,
        tenant=principal.tenant,
        principal_id=principal.id,
    )
    return success_envelope(FeedbackView.of(record), trace_id=trace_id_of(request))


@router.get(
    "/negative/candidates",
    response_model=ApiEnvelope[NegativeCandidatesView],
    summary="差评汇总:黄金数据集候选用例(json / csv 结构化导出)",
)
def list_negative_candidates(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
    limit: int = Query(default=20, ge=1, le=200),
    export_format: str | None = Query(default=None, pattern="^(json|csv)$", alias="format"),
) -> Response | ApiEnvelope[NegativeCandidatesView]:
    """同问题差评聚成待改进项;``format=csv`` 时返回可直接留档的 CSV 文件。

    只统计本租户;成员只统计自己的差评,管理员看本租户全部。
    """
    service: FeedbackService = request.app.state.feedback_service
    principal_id = _visible_principal_id(principal)
    candidates = service.negative_candidates(
        tenant=principal.tenant, limit=limit, principal_id=principal_id
    )
    if export_format == "csv":
        return Response(
            content=_render_candidates_csv(candidates),
            media_type=_CANDIDATE_CSV_CONTENT_TYPE,
            headers={"Content-Disposition": 'attachment; filename="negative_candidates.csv"'},
        )
    data = NegativeCandidatesView(
        total=service.count(
            tenant=principal.tenant,
            verdict=FeedbackVerdict.USELESS,
            principal_id=principal_id,
        ),
        limit=limit,
        items=[GoldenCaseCandidateView.of(candidate) for candidate in candidates],
    )
    return success_envelope(data, trace_id=trace_id_of(request))


def _render_candidates_csv(candidates: list[GoldenCaseCandidate]) -> str:
    """把候选聚合成 CSV(表头 + 数据行都走 csv.writer);前缀 BOM 供 Excel 直开中文。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CANDIDATE_CSV_HEADER.split(","))
    for candidate in candidates:
        comments = " | ".join(candidate.comments)
        origin_query_ids = " ".join(origin.query_id for origin in candidate.origins)
        writer.writerow(
            [
                candidate.id,
                candidate.question,
                candidate.occurrences,
                candidate.failure,
                candidate.first_seen.isoformat(),
                candidate.last_seen.isoformat(),
                comments,
                origin_query_ids,
            ]
        )
    return "\ufeff" + buffer.getvalue()


@router.get("", response_model=ApiEnvelope[FeedbackViewList], summary="反馈列表(最近优先)")
def list_feedback(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
    verdict: FeedbackVerdict | None = None,
    query_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> ApiEnvelope[FeedbackViewList]:
    """查询日志反向可查反馈:成员只看自己的,管理员看本租户全部。"""
    service: FeedbackService = request.app.state.feedback_service
    principal_id = _visible_principal_id(principal)
    if query_id is not None:
        record = service.get_by_query_id(
            query_id, tenant=principal.tenant, principal_id=principal_id
        )
        items = [record] if record is not None and verdict in (None, record.verdict) else []
        data = FeedbackViewList(
            total=len(items), limit=limit, items=[FeedbackView.of(item) for item in items]
        )
        return success_envelope(data, trace_id=trace_id_of(request))

    records = service.list_recent(
        tenant=principal.tenant,
        limit=limit,
        verdict=verdict,
        principal_id=principal_id,
    )
    data = FeedbackViewList(
        total=service.count(
            tenant=principal.tenant,
            verdict=verdict,
            principal_id=principal_id,
        ),
        limit=limit,
        items=[FeedbackView.of(record) for record in records],
    )
    return success_envelope(data, trace_id=trace_id_of(request))


@router.get(
    "/{feedback_id}",
    response_model=ApiEnvelope[FeedbackView],
    summary="按 feedback_id 查询反馈",
)
def get_feedback(
    request: Request,
    feedback_id: str,
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApiEnvelope[FeedbackView]:
    service: FeedbackService = request.app.state.feedback_service
    record = service.get(
        feedback_id,
        tenant=principal.tenant,
        principal_id=_visible_principal_id(principal),
    )
    if record is None:
        raise NotFoundError("反馈不存在", details={"feedback_id": feedback_id})
    return success_envelope(FeedbackView.of(record), trace_id=trace_id_of(request))
