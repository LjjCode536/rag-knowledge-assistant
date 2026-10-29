"""知识库文档端点:上传即解析(M1 同步),状态与解析产物全程可查。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request, UploadFile
from pydantic import BaseModel

from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of
from app.domain.documents import DocumentFormat, DocumentRecord, DocumentStatus, ElementType
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class DocumentView(BaseModel):
    """文档档案视图:状态机位置、业务元数据与失败原因。"""

    id: str
    file_name: str
    file_type: DocumentFormat
    size_bytes: int
    tenant: str
    department: str | None = None
    doc_type: str | None = None
    version: str
    status: DocumentStatus
    error_message: str | None = None
    page_count: int | None = None
    element_count: int | None = None
    chunk_count: int | None = None
    created_at: datetime
    parsed_at: datetime | None = None
    chunked_at: datetime | None = None
    failed_at: datetime | None = None

    @classmethod
    def of(cls, record: DocumentRecord) -> DocumentView:
        return cls(
            id=record.id,
            file_name=record.file_name,
            file_type=record.file_type,
            size_bytes=record.size_bytes,
            tenant=record.tenant,
            department=record.department,
            doc_type=record.doc_type,
            version=record.version,
            status=record.status,
            error_message=record.error_message,
            page_count=record.page_count,
            element_count=record.element_count,
            chunk_count=record.chunk_count,
            created_at=record.created_at,
            parsed_at=record.parsed_at,
            chunked_at=record.chunked_at,
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


def _blank_to_none(value: str | None) -> str | None:
    """表单空值视为未提供,而非空字符串元数据。"""
    value = (value or "").strip()
    return value or None


@router.post(
    "",
    response_model=ApiEnvelope[DocumentView],
    status_code=201,
    summary="上传知识库文档(同步解析)",
)
def upload_document(
    request: Request,
    file: UploadFile,
    tenant: Annotated[str, Form()] = "default",
    department: Annotated[str | None, Form()] = None,
    doc_type: Annotated[str | None, Form()] = None,
    version: Annotated[str, Form()] = "v1",
) -> ApiEnvelope[DocumentView]:
    """上传 txt / md / html / docx / pdf,请求内完成解析。

    元数据字段(租户 / 部门 / 文档类型 / 版本)进入文档档案并随切片冗余;
    解析失败同样返回 201,`data.status` 为 `failed` 且 `error_message` 带原因;
    不支持的扩展名返回 415,不生成档案。
    """
    service: IngestionService = request.app.state.ingestion_service
    content = file.file.read()
    record = service.ingest(
        file_name=file.filename or "",
        content=content,
        tenant=tenant.strip() or "default",
        department=_blank_to_none(department),
        doc_type=_blank_to_none(doc_type),
        version=version.strip() or "v1",
    )
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
