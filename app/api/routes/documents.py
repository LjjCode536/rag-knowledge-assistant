"""知识库文档端点:上传登记任务句柄,解析 → 切片 → 入库的进度由状态机可查(M7 异步 ingestion)。

端点一览:

- ``POST   /api/v1/documents``        上传(登记,后台入库);
- ``GET    /api/v1/documents``        **文档清单**(T17):按主体租户收敛,可再按
  状态 / 关键字 / 部门 / 文档类型筛选,分页返回并给出总数;
- ``GET    /api/v1/documents/{id}``   查询文档状态(入库进度);
- ``GET    /api/v1/documents/{id}/elements``  查询解析产物;
- ``DELETE /api/v1/documents/{id}``   删除文档(切片与向量连带清理)。

读路径的租户边界来自登录主体构造的授权范围:跨租户对象一律 404(ADR-0020)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, Request, UploadFile
from pydantic import BaseModel

from app.api.dependencies import reject_tenant_parameter, require_principal
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of
from app.domain.documents import DocumentFormat, DocumentRecord, DocumentStatus, ElementType
from app.domain.identity import Principal
from app.services.access import AccessService
from app.services.document_catalog import DocumentCatalogService
from app.services.ingestion import IngestionService
from app.services.lifecycle import DocumentLifecycleService

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
    is_latest: bool = True
    page_count: int | None = None
    element_count: int | None = None
    chunk_count: int | None = None
    created_at: datetime
    parsed_at: datetime | None = None
    chunked_at: datetime | None = None
    indexed_at: datetime | None = None
    failed_at: datetime | None = None
    superseded_at: datetime | None = None
    deleted_at: datetime | None = None

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
            is_latest=record.is_latest,
            page_count=record.page_count,
            element_count=record.element_count,
            chunk_count=record.chunk_count,
            created_at=record.created_at,
            parsed_at=record.parsed_at,
            chunked_at=record.chunked_at,
            indexed_at=record.indexed_at,
            failed_at=record.failed_at,
            superseded_at=record.superseded_at,
            deleted_at=record.deleted_at,
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
    """解析产物视图:文档 id、页数与元素。"""

    document_id: str
    page_count: int | None = None
    elements: list[ElementView]


class DocumentListView(BaseModel):
    """知识库文档清单:符合条件的总数 + 当前页条数。

    ``total`` 是**符合筛选条件的总数**(不受分页影响),故前端能据此算出
    总页数;``items`` 是当前页的文档档案。
    """

    total: int
    limit: int
    offset: int
    items: list[DocumentView]


def _blank_to_none(value: str | None) -> str | None:
    """表单空值视为未提供,而非空字符串元数据。"""
    value = (value or "").strip()
    return value or None


@router.post(
    "",
    response_model=ApiEnvelope[DocumentView],
    status_code=201,
    summary="上传知识库文档(登记,后台入库)",
)
def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile,
    tenant: Annotated[str, Form()] = "default",
    department: Annotated[str | None, Form()] = None,
    doc_type: Annotated[str | None, Form()] = None,
    version: Annotated[str, Form()] = "v1",
) -> ApiEnvelope[DocumentView]:
    """上传 txt / md / html / docx / pdf,立即返回任务句柄(文档档案)。

    默认(`ingestion_async=true`,见 ADR-0012):请求只登记档案与落盘,
    解析 → 切片 → 向量化转后台任务,解析 / 切片失败落 `failed` 带
    `error_message`,可重传重试;上游向量化失败停 `chunked`,可经
    `POST /{id}/embeddings` 重试。进度经 `GET /{document_id}` 查询,
    状态机位置全程可查,到 indexed 即可问答。
    `ingestion_async=false` 时同一服务在请求内同步完成解析(行为一致口径)。

    元数据字段(租户 / 部门 / 文档类型 / 版本)进入文档档案并随切片冗余;
    不支持的扩展名返回 415,不生成档案。
    """
    service: IngestionService = request.app.state.ingestion_service
    content = file.file.read()
    record = service.register(
        file_name=file.filename or "",
        content=content,
        tenant=tenant.strip() or "default",
        department=_blank_to_none(department),
        doc_type=_blank_to_none(doc_type),
        version=version.strip() or "v1",
    )
    if request.app.state.settings.ingestion_async:
        pipeline = request.app.state.ingestion_pipeline
        background_tasks.add_task(pipeline.run, record.id)
    else:
        # 同步形态:与异步管线共用同一 parse,落点与失败语义一致(ADR-0012)
        record = service.parse(record.id)
    return success_envelope(DocumentView.of(record), trace_id=trace_id_of(request))


@router.get(
    "",
    response_model=ApiEnvelope[DocumentListView],
    summary="知识库文档清单(按创建时间倒序,支持筛选与分页)",
    dependencies=[Depends(reject_tenant_parameter)],
)
def list_documents(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
    status: Annotated[DocumentStatus | None, Query(description="按状态精确匹配")] = None,
    keyword: Annotated[
        str | None, Query(description="按文件名子串匹配(大小写不敏感)")
    ] = None,
    department: Annotated[str | None, Query(description="按部门精确匹配")] = None,
    doc_type: Annotated[str | None, Query(description="按文档类型精确匹配")] = None,
    include_history: Annotated[
        bool, Query(description="含退位与已删除的旧版本(默认只列现行版本)")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200, description="每页条数")] = 50,
    offset: Annotated[int, Query(ge=0, description="偏移量")] = 0,
) -> ApiEnvelope[DocumentListView]:
    """列出授权范围内的知识库文档,并给出符合筛选条件的总数。

    租户只来自登录主体,端点不接受租户查询参数(携带 ``tenant`` 即 422);
    清单与总数都收敛在同一主体的授权范围内,不会看到异租户文档。

    默认**只列现行版本**:退位的旧版本与已删除的文档不进清单,给
    ``include_history=true`` 可查历史(ADR-0015 的显式例外:知识库服务
    必须能列举自己的文档)。

    排序固定为创建时间倒序(最新在前);创建时间相同时按 id 兜底,次序
    确定,不依赖存储实现的偶然顺序。

    筛选条件按"与"组合:状态 / 关键字 / 部门 / 文档类型;关键字命中
    文件名子串且大小写不敏感。

    分页越界(偏移超出总数)返回空清单与正确总数,不报错。
    """
    service: DocumentCatalogService = request.app.state.document_catalog_service
    access: AccessService = request.app.state.access_service
    scope = access.scope_for(principal, None)
    page = service.list_documents(
        scope=scope,
        status=status,
        keyword=keyword,
        department=department,
        doc_type=doc_type,
        latest_only=not include_history,
        limit=limit,
        offset=offset,
    )
    data = DocumentListView(
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        items=[DocumentView.of(record) for record in page.items],
    )
    return success_envelope(data, trace_id=trace_id_of(request))


@router.get("/{document_id}", response_model=ApiEnvelope[DocumentView], summary="查询文档状态")
def get_document(
    request: Request,
    document_id: str,
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApiEnvelope[DocumentView]:
    access: AccessService = request.app.state.access_service
    scope = access.scope_for(principal, None)
    record = access.require_document(scope=scope, document_id=document_id)
    return success_envelope(DocumentView.of(record), trace_id=trace_id_of(request))


@router.get(
    "/{document_id}/elements",
    response_model=ApiEnvelope[ElementsView],
    summary="查询解析产物(结构化元素)",
)
def get_document_elements(
    request: Request,
    document_id: str,
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApiEnvelope[ElementsView]:
    access: AccessService = request.app.state.access_service
    scope = access.scope_for(principal, None)
    record = access.require_document(scope=scope, document_id=document_id)
    service: IngestionService = request.app.state.ingestion_service
    views = [ElementView(**element.model_dump()) for element in service.get_elements(document_id)]
    data = ElementsView(document_id=record.id, page_count=record.page_count, elements=views)
    return success_envelope(data, trace_id=trace_id_of(request))


@router.delete(
    "/{document_id}",
    response_model=ApiEnvelope[DocumentView],
    summary="删除文档(切片与向量连带清理)",
)
def delete_document(request: Request, document_id: str) -> ApiEnvelope[DocumentView]:
    """删除知识库文档:状态机迁移到 deleted 终态,切片、向量、关键词通道条目与
    落盘文件连带清理;档案保留可查(终态、失败原因)、重复删除幂等。
    清理失败返回 `DOCUMENT_DELETE_FAILED`,档案原状保留、可重试。
    """
    service: DocumentLifecycleService = request.app.state.lifecycle_service
    record = service.delete(document_id)
    return success_envelope(DocumentView.of(record), trace_id=trace_id_of(request))
