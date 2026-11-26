"""向量化端点:批量向量化已切片文档并写入内存向量库(M3 基线)。

一次调用 = 整个文档的切片批量送向量模型;成功落 indexed,失败不动状态。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.dependencies import require_writable_document
from app.api.routes.documents import DocumentView
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.errors import InvalidDocumentStateError
from app.core.tracing import trace_id_of
from app.domain.documents import DocumentRecord
from app.domain.state_machine import StateTransitionError
from app.services.embedding import EmbeddingRunResult, EmbeddingService

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class EmbeddingRunView(BaseModel):
    """一次向量化运行视图:档案最新状态 + 生效模型 / 来源身份 + 用量与延迟。"""

    document: DocumentView
    model: str
    embedding_version: str
    embedding_dimension: int
    chunk_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float

    @classmethod
    def of(cls, result: EmbeddingRunResult) -> EmbeddingRunView:
        record = result.record
        return cls(
            document=DocumentView.of(record),
            model=result.model,
            embedding_version=result.embedding.version,
            embedding_dimension=result.embedding.dimension,
            chunk_count=result.chunk_count,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
            latency_ms=result.latency_ms,
        )


@router.post(
    "/{document_id}/embeddings",
    response_model=ApiEnvelope[EmbeddingRunView],
    summary="向量化并入库(批量,内存向量库)",
)
async def embed_document(
    request: Request,
    document_id: str,
    record: Annotated[DocumentRecord, Depends(require_writable_document)],
) -> ApiEnvelope[EmbeddingRunView]:
    """批量向量化已切片文档,写入内存向量库;失败落统一上游错误信封。

    只有文档所有者或租户管理员可向量化;其他主体按对象不可见返回 404。
    """
    service: EmbeddingService = request.app.state.embedding_service
    try:
        result = await service.embed_document(record.id)
    except StateTransitionError as exc:
        # 状态机不变量被破坏属程序错误,但对外仍回稳定错误码而非 500 堆栈
        raise InvalidDocumentStateError(
            details={"document_id": document_id, "reason": str(exc)}
        ) from None
    return success_envelope(EmbeddingRunView.of(result), trace_id=trace_id_of(request))
