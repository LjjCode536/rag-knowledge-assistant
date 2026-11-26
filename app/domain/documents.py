"""知识库文档与解析结果的领域模型(教材第 40 章 ParsedDocument 的生产版)。

后续的切分、向量化与检索只依赖本层结构,不关心原始文件格式;
框架类型按 ADR-0001 只出现在实现层(app/integrations)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.errors import UnsupportedFormatError
from app.domain.chunks import ChunkQualityWarning


class DocumentFormat(StrEnum):
    """知识库文档格式,决定解析器选择。"""

    TXT = "txt"
    MD = "md"
    HTML = "html"
    DOCX = "docx"
    PDF = "pdf"

    @classmethod
    def from_filename(cls, file_name: str) -> DocumentFormat:
        """按扩展名识别格式(`.markdown` / `.htm` 视为同义)。未知扩展名抛 415。"""
        suffix = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        alias = {"markdown": "md", "htm": "html"}.get(suffix, suffix)
        try:
            return cls(alias)
        except ValueError:
            raise UnsupportedFormatError(details={"file_name": file_name}) from None


class ElementType(StrEnum):
    """解析元素类型。"""

    TITLE = "title"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    CODE = "code"


class DocumentElement(BaseModel):
    """解析出的结构单元:类型 + 内容 + 定位元数据(页码 / 行号 / 标题层级)。"""

    element_type: ElementType
    content: str
    order_index: int = 0
    page_number: int | None = None
    heading_level: int | None = None
    line: int | None = None


class ParsedDocument(BaseModel):
    """统一解析结果:元素带类型与页码等定位元数据。"""

    document_id: str
    file_name: str
    file_type: DocumentFormat
    page_count: int | None = None
    elements: list[DocumentElement] = Field(default_factory=list)


class DocumentStatus(StrEnum):
    """知识库文档生命周期状态。

    M1 覆盖 uploaded → parsing → parsed / failed;M2 切片落点为 chunked
    (见 ADR-0002);M3 覆盖 chunked → embedding → indexed(见 ADR-0003)。
    """

    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    CHUNKING = "chunking"
    CHUNKED = "chunked"
    EMBEDDING = "embedding"
    INDEXED = "indexed"
    FAILED = "failed"
    # T10 文档生命周期:superseded 承接版本退位(新版本上新、旧版本让位),
    # deleted 承接文档删除;两者均为终态,迁移表见 state_machine / ADR-0009。
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class DocumentFilter(BaseModel):
    """知识库文档清单的筛选与分页条件(T17)。

    清单端点的查询口径收口在本模型:路由层把查询参数装配成它,仓储实现
    只认它 —— 内存实现与 PostgreSQL 实现因此共享同一套口径(ADR-0001:
    换实现只换实现)。

    字段语义:

    - ``tenant`` / ``department`` / ``doc_type``:精确匹配(缺省不限);
    - ``status``:状态精确匹配(缺省不限);
    - ``keyword``:命中文件名的子串,**大小写不敏感**;
    - ``latest_only``:只列现行版本(``is_latest = true``),缺省为 ``True``
      —— 退位的旧版本与已删除的文档不进默认清单;给 ``False`` 可查历史;
    - ``limit`` / ``offset``:分页;``limit`` 缺省为 50,``offset`` 缺省为 0。
      ``offset`` 超出总数时返回空清单(不报错),``total`` 仍是符合条件的总数。
    - ``document_ids``:授权解析出的允许集合(IN 语义);``None`` 表示不设
      集合条件,**空集合表示一条都列不出来**。
    """

    tenant: str | None = None
    status: DocumentStatus | None = None
    keyword: str | None = None
    department: str | None = None
    doc_type: str | None = None
    document_ids: frozenset[str] | None = None
    latest_only: bool = True
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class DocumentRecord(BaseModel):
    """知识库文档档案:状态机当前位置、失败原因、业务元数据与解析产物。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    file_name: str
    file_type: DocumentFormat
    size_bytes: int
    status: DocumentStatus = DocumentStatus.UPLOADED
    error_message: str | None = None
    # ===== 版本生命周期(T10):同一标识(租户 + 文件名)的新版本上新即退位 =====
    is_latest: bool = True
    superseded_at: datetime | None = None
    deleted_at: datetime | None = None
    # ===== 知识库文档元数据(切片时冗余进切片元数据)=====
    tenant: str = "default"
    # 文档所有者:上传该文档的主体;历史数据未知时为 None(ADR-0020 / #29)
    owner_id: str | None = None
    department: str | None = None
    doc_type: str | None = None
    version: str = "v1"
    page_count: int | None = None
    element_count: int | None = None
    parsed: ParsedDocument | None = None
    # ===== 切片产物摘要 =====
    chunk_count: int | None = None
    chunk_warnings: list[ChunkQualityWarning] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    parsed_at: datetime | None = None
    chunked_at: datetime | None = None
    indexed_at: datetime | None = None
    failed_at: datetime | None = None
