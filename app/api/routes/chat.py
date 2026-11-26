"""问答端点:检索 → 有据回答 + 引用,资料不足统一拒答(T05 / M3 基线下半)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.dependencies import require_principal
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of
from app.domain.identity import Principal, ScopeNarrowing
from app.domain.qa import Citation, GenerationUsage, GeneratorKind, RefusalReason
from app.services.access import AccessService
from app.services.answering import AnswerRunResult, AnswerService

router = APIRouter(prefix="/api/v1", tags=["qa"])


class ChatRequest(BaseModel):
    """问答请求:问题 + 可选 top-k 与授权范围内的收窄条件。

    租户不由调用方声明(携带即 422);服务端从主体构造授权范围。
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=50)
    filters: ScopeNarrowing | None = None

    @field_validator("question")
    @classmethod
    def _question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question 不能为空白")
        return value


class CitationView(BaseModel):
    """引用视图:编号 + 来源切片定位(文档 / 标题 / 页码)+ 相似度分数。"""

    index: int
    chunk_id: str
    document_id: str
    file_name: str | None = None
    title: str | None = None
    page_number: int | None = None
    score: float

    @classmethod
    def of(cls, citation: Citation) -> CitationView:
        return cls(**citation.model_dump())


class ChatView(BaseModel):
    """一次问答视图:答案、拒答原因、引用、生成器留痕与端到端延迟。"""

    query_id: str
    question: str
    answer: str
    refused: bool
    refusal_reason: RefusalReason | None = None
    top_k: int
    min_score: float
    filters: dict[str, str]
    citations: list[CitationView]
    latency_ms: float
    generator: GeneratorKind
    token_usage: GenerationUsage
    rewritten_question: str | None = None
    expanded_questions: list[str]
    # T15:True 表示本次回答来自答档缓存(同题二次询问)
    cache_hit: bool = False

    @classmethod
    def of(cls, result: AnswerRunResult) -> ChatView:
        log = result.log
        return cls(
            query_id=log.query_id,
            question=log.question,
            answer=log.answer,
            refused=log.refused,
            refusal_reason=log.refusal_reason,
            top_k=log.top_k,
            min_score=log.min_score,
            filters=log.filters,
            citations=[CitationView.of(citation) for citation in result.citations],
            latency_ms=log.latency_ms,
            generator=log.generator,
            token_usage=log.token_usage,
            rewritten_question=log.rewritten_question,
            expanded_questions=log.expanded_questions,
            cache_hit=result.cache_hit,
        )


@router.post("/chat", response_model=ApiEnvelope[ChatView], summary="有据回答 + 引用")
async def chat(
    request: Request,
    body: ChatRequest,
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApiEnvelope[ChatView]:
    """基于知识库资料回答;检索低分或无命中时统一拒答并记录查询日志。"""
    service: AnswerService = request.app.state.answer_service
    access: AccessService = request.app.state.access_service
    scope = access.scope_for(principal, body.filters)
    trace_id = trace_id_of(request)
    result = await service.ask(
        body.question,
        scope=scope,
        principal_id=principal.id,
        top_k=body.top_k,
        trace_id=trace_id,
    )
    return success_envelope(ChatView.of(result), trace_id=trace_id)
