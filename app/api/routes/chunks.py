"""切片端点:按策略与参数切分文档,切片与质量告警全程可查。

策略 / 尺寸 / 重叠 参数不传时回退统一配置;对同一文档重复调用即重切(换策略即时生效)。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator

from app.api.routes.documents import DocumentView
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.errors import InvalidDocumentStateError
from app.core.tracing import trace_id_of
from app.domain.chunks import Chunk, ChunkingStrategy, ChunkQualityWarning
from app.domain.documents import DocumentStatus
from app.domain.state_machine import StateTransitionError
from app.services.chunking import ChunkingService, ChunkRunResult
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class ChunkView(BaseModel):
    """切片视图:内容 + 完整切片元数据 + 来源定位。"""

    chunk_id: str
    document_id: str
    index: int
    content: str
    tenant: str
    department: str | None = None
    doc_type: str | None = None
    version: str
    page_number: int | None = None
    title: str | None = None
    element_index_start: int
    element_index_end: int

    @classmethod
    def of(cls, chunk: Chunk) -> ChunkView:
        return cls(**chunk.model_dump())


class ChunkRunView(BaseModel):
    """一次切片运行视图:生效参数 + 档案最新状态 + 切片与告警。"""

    document: DocumentView
    strategy: ChunkingStrategy
    chunk_size: int
    chunk_overlap: int
    chunk_count: int
    warnings: list[ChunkQualityWarning]
    chunks: list[ChunkView]

    @classmethod
    def of(cls, result: ChunkRunResult) -> ChunkRunView:
        record = result.record
        return cls(
            document=DocumentView.of(record),
            strategy=result.strategy,
            chunk_size=result.chunk_size,
            chunk_overlap=result.chunk_overlap,
            chunk_count=len(result.chunks),
            warnings=result.warnings,
            chunks=[ChunkView.of(chunk) for chunk in result.chunks],
        )


class ChunksListView(BaseModel):
    """查询切片视图:档案状态 + 全量切片与告警。"""

    document_id: str
    status: DocumentStatus
    chunk_count: int
    warnings: list[ChunkQualityWarning]
    chunks: list[ChunkView]


class ChunkRequest(BaseModel):
    """切片参数(全部可选):不传回退统一配置。"""

    strategy: ChunkingStrategy | None = None
    chunk_size: int | None = Field(default=None, ge=1)
    chunk_overlap: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _overlap_must_be_smaller(self) -> ChunkRequest:
        if (
            self.chunk_size is not None
            and self.chunk_overlap is not None
            and self.chunk_overlap >= self.chunk_size
        ):
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return self


@router.post(
    "/{document_id}/chunks",
    response_model=ApiEnvelope[ChunkRunView],
    summary="切分文档(策略与参数可选,重切即换)",
)
def chunk_document(
    request: Request, document_id: str, body: ChunkRequest | None = None
) -> ApiEnvelope[ChunkRunView]:
    """按给定策略与参数切分已解析的文档;失败落入 failed 状态,原因可查。"""
    service: ChunkingService = request.app.state.chunking_service
    try:
        result = service.chunk(
            document_id,
            strategy=body.strategy if body else None,
            chunk_size=body.chunk_size if body else None,
            chunk_overlap=body.chunk_overlap if body else None,
        )
    except StateTransitionError as exc:
        # 状态机不变量被破坏属程序错误,但对外仍回稳定错误码而非 500 堆栈
        raise InvalidDocumentStateError(
            details={"document_id": document_id, "reason": str(exc)}
        ) from None
    return success_envelope(ChunkRunView.of(result), trace_id=trace_id_of(request))


@router.get(
    "/{document_id}/chunks",
    response_model=ApiEnvelope[ChunksListView],
    summary="查询切片(全量,含告警)",
)
def get_document_chunks(request: Request, document_id: str) -> ApiEnvelope[ChunksListView]:
    service: IngestionService = request.app.state.ingestion_service
    chunking: ChunkingService = request.app.state.chunking_service
    record = service.get(document_id)
    views = [ChunkView.of(chunk) for chunk in chunking.get_chunks(document_id)]
    data = ChunksListView(
        document_id=record.id,
        status=record.status,
        chunk_count=record.chunk_count or 0,
        warnings=record.chunk_warnings,
        chunks=views,
    )
    return success_envelope(data, trace_id=trace_id_of(request))
