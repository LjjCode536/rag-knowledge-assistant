"""检索端点:相似度检索 top-k,切片带分数与完整元数据(M3 基线上半)。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.routes.chunks import ChunkView
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of
from app.domain.retrieval import ChunkFilter, ScoredChunk
from app.services.retrieval import RetrievalService

router = APIRouter(prefix="/api/v1", tags=["retrieval"])


class SearchRequest(BaseModel):
    """检索请求:查询 + top-k + 检索前元数据过滤(权限维度)。"""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    filters: ChunkFilter | None = None


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
async def search(request: Request, body: SearchRequest) -> ApiEnvelope[SearchResultView]:
    """按相似度检索切片:过滤发生在检索前,同题查询按分数降序。"""
    service: RetrievalService = request.app.state.retrieval_service
    hits = await service.search(body.query, top_k=body.top_k, filters=body.filters)
    data = SearchResultView(
        query=body.query,
        top_k=body.top_k,
        total=len(hits),
        filters=(body.filters or ChunkFilter()).effective(),
        hits=[_view_of(hit) for hit in hits],
    )
    return success_envelope(data, trace_id=trace_id_of(request))
