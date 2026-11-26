"""检索端点:相似度检索 top-k,切片带分数与完整元数据(M3 基线上半)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import require_principal
from app.api.routes.chunks import ChunkView
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of
from app.domain.identity import Principal, ScopeNarrowing
from app.domain.retrieval import ScoredChunk
from app.services.access import AccessService
from app.services.retrieval import RetrievalService

router = APIRouter(prefix="/api/v1", tags=["retrieval"])


class SearchRequest(BaseModel):
    """检索请求:查询 + top-k + 授权范围内的收窄条件。

    租户不由调用方声明(携带即 422);服务端从主体构造授权范围,
    ``filters`` 只能在其内收窄部门 / 文档类型 / 版本 / 文档。
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    filters: ScopeNarrowing | None = None


class SearchHitView(BaseModel):
    """命中视图:相似度分数 + 完整切片元数据。"""

    score: float
    chunk: ChunkView


class SearchResultView(BaseModel):
    """检索结果视图:生效参数 + 按分数降序的命中。"""

    query: str
    top_k: int
    total: int
    filters: dict[str, str]
    hits: list[SearchHitView]


def _view_of(hit: ScoredChunk) -> SearchHitView:
    return SearchHitView(score=round(hit.score, 6), chunk=ChunkView.of(hit.chunk))


@router.post("/search", response_model=ApiEnvelope[SearchResultView], summary="相似度检索")
async def search(
    request: Request,
    body: SearchRequest,
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApiEnvelope[SearchResultView]:
    """按相似度检索切片:授权范围过滤发生在检索前,同题查询按分数降序。"""
    service: RetrievalService = request.app.state.retrieval_service
    access: AccessService = request.app.state.access_service
    scope = access.scope_for(principal, body.filters)
    hits = await service.search(body.query, top_k=body.top_k, scope=scope)
    data = SearchResultView(
        query=body.query,
        top_k=body.top_k,
        total=len(hits),
        filters=scope.effective(),
        hits=[_view_of(hit) for hit in hits],
    )
    return success_envelope(data, trace_id=trace_id_of(request))
