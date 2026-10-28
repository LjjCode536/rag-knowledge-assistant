"""知识库文档端点:上传即解析(M1 同步),状态与解析产物全程可查。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request, UploadFile
from pydantic import BaseModel

from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of
from app.domain.documents import DocumentFormat, DocumentRecord, DocumentStatus, ElementType
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class DocumentView(BaseModel):
    """文档档案视图:状态机位置与失败原因。"""

    id: str
    file_name: str
    file_type: DocumentFormat
    size_bytes: int
    status: DocumentStatus
    error_message: str | None = None
    page_count: int | None = None
    element_count: int | None = None
    created_at: datetime
    parsed_at: datetime | None = None
    failed_at: datetime | None = None

    @classmethod
    def of(cls, record: DocumentRecord) -> DocumentView:
        return cls(
            id=record.id,
            file_name=record.file_name,
            file_type=record.file_type,
            size_bytes=record.size_bytes,
            status=record.status,
            error_message=record.error_message,
            page_count=record.page_count,
            element_count=record.element_count,
            created_at=record.created_at,
            parsed_at=record.parsed_at,
            failed_at=record.failed_at,
        )


class ElementView(BaseModel):
    """解析元素视图:类型 + 内容 + 定位元数据。"""

    element_type: ElementType
    content: str
    order_index: int
    page_number: int | None = None
    heading_level: int | None = None
    line: int | None = None


class ElementsView(BaseModel):
    document_id: str
    page_count: int | None = None
    elements: list[ElementView]


@router.post(
    "",
    response_model=ApiEnvelope[DocumentView],
    status_code=201,
    summary="上传知识库文档(同步解析)",
)
def upload_document(request: Request, file: UploadFile) -> ApiEnvelope[DocumentView]:
    """上传 txt / md / html / docx / pdf,请求内完成解析。

    解析失败同样返回 201,`data.status` 为 `failed` 且 `error_message` 带原因;
    不支持的扩展名返回 415,不生成档案。
    """
    service: IngestionService = request.app.state.ingestion_service
    content = file.file.read()
    record = service.ingest(file_name=file.filename or "", content=content)
    return success_envelope(DocumentView.of(record), trace_id=trace_id_of(request))


@router.get("/{document_id}", response_model=ApiEnvelope[DocumentView], summary="查询文档状态")
def get_document(request: Request, document_id: str) -> ApiEnvelope[DocumentView]:
    service: IngestionService = request.app.state.ingestion_service
    record = service.get(document_id)
    return success_envelope(DocumentView.of(record), trace_id=trace_id_of(request))


@router.get(
    "/{document_id}/elements",
    response_model=ApiEnvelope[ElementsView],
    summary="查询解析产物(结构化元素)",
)
def get_document_elements(request: Request, document_id: str) -> ApiEnvelope[ElementsView]:
    service: IngestionService = request.app.state.ingestion_service
    record = service.get(document_id)
    views = [ElementView(**element.model_dump()) for element in service.get_elements(document_id)]
    data = ElementsView(document_id=record.id, page_count=record.page_count, elements=views)
    return success_envelope(data, trace_id=trace_id_of(request))
