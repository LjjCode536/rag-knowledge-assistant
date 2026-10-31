"""PostgreSQL 行 ↔ 领域模型的映射层:仓储实现的样板收口在一处。

映射由 pydantic 承担(别名 + 枚举还原 + jsonb 反序列化),不再逐字段手写
``row["x"]`` 拼装:列名与领域字段名同名的直接取,不同的按显式别名映射
(``chunk_index`` → ``index``、``file_type`` → ``DocumentFormat`` 等)。

边界:列名只在本模块出现一次,领域模型不感知数据库(ADR-0001);
手写 SQL 与 DDL(含 ``bigserial`` 时序列、部分索引、``vector`` 列)保持不变,
不引入 ORM。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.chunks import Chunk, ChunkQualityWarning
from app.domain.documents import (
    DocumentFormat,
    DocumentRecord,
    DocumentStatus,
    ParsedDocument,
)
from app.domain.feedback import FeedbackRecord, FeedbackVerdict
from app.domain.qa import (
    GenerationUsage,
    GeneratorKind,
    QueryHit,
    QueryLog,
    RefusalReason,
)

# ===== 领域模型 ↔ 数据行的别名视图 =====


class _RowModel(BaseModel):
    """行视图基类:按字段名取值,忽略模型默认值(数据行为唯一事实)。"""

    model_config = ConfigDict(populate_by_name=True)


class _DocumentRow(_RowModel):
    id: str
    file_name: str
    file_type: DocumentFormat
    size_bytes: int
    status: DocumentStatus
    error_message: str | None = None
    is_latest: bool = True
    superseded_at: datetime | None = None
    deleted_at: datetime | None = None
    tenant: str = "default"
    department: str | None = None
    doc_type: str | None = None
    version: str = "v1"
    page_count: int | None = None
    element_count: int | None = None
    parsed: ParsedDocument | None = None
    chunk_count: int | None = None
    chunk_warnings: list[ChunkQualityWarning] = Field(default_factory=list)
    created_at: datetime
    parsed_at: datetime | None = None
    chunked_at: datetime | None = None
    indexed_at: datetime | None = None
    failed_at: datetime | None = None

    @field_validator("chunk_warnings", mode="before")
    @classmethod
    def _null_warnings_are_empty(cls, value: Any) -> Any:
        """库里 chunk_warnings 可能是 NULL:按空告警列表处理。"""
        return [] if value is None else value


class _ChunkRow(_RowModel):
    chunk_id: str
    document_id: str
    index: int = Field(validation_alias="chunk_index")
    content: str
    tenant: str
    department: str | None = None
    doc_type: str | None = None
    version: str
    page_number: int | None = None
    title: str | None = None
    element_index_start: int
    element_index_end: int


class _QueryLogRow(_RowModel):
    query_id: str
    trace_id: str
    question: str
    answer: str
    refused: bool
    refusal_reason: RefusalReason | None = None
    hits: list[QueryHit] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    top_k: int
    min_score: float
    latency_ms: float
    generator: GeneratorKind
    token_usage: GenerationUsage = Field(default_factory=GenerationUsage)
    rewritten_question: str | None = None
    expanded_questions: list[str] = Field(default_factory=list)
    created_at: datetime


class _FeedbackRow(_RowModel):
    feedback_id: str
    query_id: str
    trace_id: str
    verdict: FeedbackVerdict
    comment: str | None = None
    created_at: datetime


# ===== 对外的行 → 领域模型 =====


def document_from_row(row: Mapping[str, Any]) -> DocumentRecord:
    """documents 行 → 档案。"""
    return _document(_DocumentRow.model_validate(dict(row)))


def _document(row: _DocumentRow) -> DocumentRecord:
    return DocumentRecord(
        id=row.id,
        file_name=row.file_name,
        file_type=row.file_type,
        size_bytes=row.size_bytes,
        status=row.status,
        error_message=row.error_message,
        is_latest=row.is_latest,
        superseded_at=row.superseded_at,
        deleted_at=row.deleted_at,
        tenant=row.tenant,
        department=row.department,
        doc_type=row.doc_type,
        version=row.version,
        page_count=row.page_count,
        element_count=row.element_count,
        parsed=row.parsed,
        chunk_count=row.chunk_count,
        chunk_warnings=row.chunk_warnings,
        created_at=row.created_at,
        parsed_at=row.parsed_at,
        chunked_at=row.chunked_at,
        indexed_at=row.indexed_at,
        failed_at=row.failed_at,
    )


def chunk_from_row(row: Mapping[str, Any]) -> Chunk:
    """chunks 行 → 切片(chunk_index 列映射为 index 字段)。"""
    parsed = _ChunkRow.model_validate(dict(row))
    return Chunk(
        chunk_id=parsed.chunk_id,
        document_id=parsed.document_id,
        index=parsed.index,
        content=parsed.content,
        tenant=parsed.tenant,
        department=parsed.department,
        doc_type=parsed.doc_type,
        version=parsed.version,
        page_number=parsed.page_number,
        title=parsed.title,
        element_index_start=parsed.element_index_start,
        element_index_end=parsed.element_index_end,
    )


def query_log_from_row(row: Mapping[str, Any]) -> QueryLog:
    """query_logs 行 → 查询日志。"""
    parsed = _QueryLogRow.model_validate(dict(row))
    return QueryLog(
        query_id=parsed.query_id,
        trace_id=parsed.trace_id,
        question=parsed.question,
        answer=parsed.answer,
        refused=parsed.refused,
        refusal_reason=parsed.refusal_reason,
        hits=parsed.hits,
        filters=parsed.filters,
        top_k=parsed.top_k,
        min_score=parsed.min_score,
        latency_ms=parsed.latency_ms,
        generator=parsed.generator,
        token_usage=parsed.token_usage,
        rewritten_question=parsed.rewritten_question,
        expanded_questions=parsed.expanded_questions,
        created_at=parsed.created_at,
    )


def feedback_from_row(row: Mapping[str, Any]) -> FeedbackRecord:
    """feedbacks 行 → 回答反馈。"""
    parsed = _FeedbackRow.model_validate(dict(row))
    return FeedbackRecord(
        feedback_id=parsed.feedback_id,
        query_id=parsed.query_id,
        trace_id=parsed.trace_id,
        verdict=parsed.verdict,
        comment=parsed.comment,
        created_at=parsed.created_at,
    )
