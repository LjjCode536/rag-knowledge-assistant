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
