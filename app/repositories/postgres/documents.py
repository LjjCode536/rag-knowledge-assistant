"""documents 表仓储:与内存基线(:class:`InMemoryDocumentRepository`)同契约。

- ``add`` 同 id 重复插入是程序错误 → ``ValueError``(内存实现同语义);
- ``save`` 按 id 覆盖式落库(状态机流转后的档案更新),不存在则新建,
  与内存 dict 赋值语义对齐;
- ``find_latest_by_file`` 只取 ``is_latest`` 且 ``(tenant, file_name)`` 命中的
  最新一条,created_at 同微秒按插入序(seq)兜底排序。
"""

from __future__ import annotations

from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.chunks import ChunkQualityWarning
from app.domain.documents import (
    DocumentFormat,
    DocumentRecord,
    DocumentStatus,
    ParsedDocument,
)
from app.repositories.postgres.base import PgDatabase, placeholder_sql

_COLUMNS = (
    "id, file_name, file_type, size_bytes, status, error_message, is_latest, "
    "superseded_at, deleted_at, tenant, department, doc_type, version, "
    "page_count, element_count, parsed, chunk_count, chunk_warnings, "
    "created_at, parsed_at, chunked_at, indexed_at, failed_at"
)

_SET_COLUMNS = (
    "file_name = EXCLUDED.file_name, file_type = EXCLUDED.file_type, "
    "size_bytes = EXCLUDED.size_bytes, status = EXCLUDED.status, "
    "error_message = EXCLUDED.error_message, is_latest = EXCLUDED.is_latest, "
    "superseded_at = EXCLUDED.superseded_at, deleted_at = EXCLUDED.deleted_at, "
    "tenant = EXCLUDED.tenant, department = EXCLUDED.department, "
    "doc_type = EXCLUDED.doc_type, version = EXCLUDED.version, "
    "page_count = EXCLUDED.page_count, element_count = EXCLUDED.element_count, "
    "parsed = EXCLUDED.parsed, chunk_count = EXCLUDED.chunk_count, "
    "chunk_warnings = EXCLUDED.chunk_warnings, created_at = EXCLUDED.created_at, "
    "parsed_at = EXCLUDED.parsed_at, chunked_at = EXCLUDED.chunked_at, "
    "indexed_at = EXCLUDED.indexed_at, failed_at = EXCLUDED.failed_at"
)

_COLUMN_LIST = _COLUMNS.split(", ")
_PLACEHOLDERS = ", ".join(["%s"] * len(_COLUMN_LIST))

_INSERT = (
    f"INSERT INTO documents ({_COLUMNS}) VALUES ({placeholder_sql(len(_COLUMN_LIST))})"
)

_UPSERT = f"{_INSERT} ON CONFLICT (id) DO UPDATE SET {_SET_COLUMNS}"

_SELECT = f"SELECT {_COLUMNS}, seq FROM documents"


def _optional_jsonb(model) -> object:
    """Pydantic 模型 → jsonb 参数;未填写(None)保持 NULL。"""
    return None if model is None else Jsonb(model.model_dump(mode="json"))


def _row_values(record: DocumentRecord) -> tuple:
    return (
        record.id,
        record.file_name,
        record.file_type.value,
        record.size_bytes,
        record.status.value,
        record.error_message,
        record.is_latest,
        record.superseded_at,
        record.deleted_at,
        record.tenant,
        record.department,
        record.doc_type,
        record.version,
        record.page_count,
        record.element_count,
        _optional_jsonb(record.parsed),
        record.chunk_count,
        Jsonb([warning.model_dump(mode="json") for warning in record.chunk_warnings]),
        record.created_at,
        record.parsed_at,
        record.chunked_at,
        record.indexed_at,
        record.failed_at,
    )


def _record_from_row(row: dict) -> DocumentRecord:
    parsed = row["parsed"]
    return DocumentRecord(
        id=row["id"],
        file_name=row["file_name"],
        file_type=DocumentFormat(row["file_type"]),
        size_bytes=row["size_bytes"],
        status=DocumentStatus(row["status"]),
        error_message=row["error_message"],
        is_latest=row["is_latest"],
        superseded_at=row["superseded_at"],
        deleted_at=row["deleted_at"],
        tenant=row["tenant"],
        department=row["department"],
        doc_type=row["doc_type"],
        version=row["version"],
        page_count=row["page_count"],
        element_count=row["element_count"],
        parsed=ParsedDocument.model_validate(parsed) if parsed is not None else None,
        chunk_count=row["chunk_count"],
        chunk_warnings=[
            ChunkQualityWarning.model_validate(warning)
            for warning in (row["chunk_warnings"] or [])
        ],
        created_at=row["created_at"],
        parsed_at=row["parsed_at"],
        chunked_at=row["chunked_at"],
        indexed_at=row["indexed_at"],
        failed_at=row["failed_at"],
    )


class PgDocumentRepository:
    """documents 表的数据仓储;锁与序列语义交由数据库事务与索引。"""

    def __init__(self, db: PgDatabase) -> None:
        self._db = db

    def add(self, record: DocumentRecord) -> None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            try:
                cursor.execute(_INSERT, _row_values(record))
            except pg_errors.UniqueViolation as exc:
                raise ValueError(f"文档已存在:{record.id}") from exc

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"{_SELECT} WHERE id = %s", (document_id,))
            row = cursor.fetchone()
        return _record_from_row(row) if row is not None else None

    def save(self, record: DocumentRecord) -> None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(_UPSERT, _row_values(record))

    def find_latest_by_file(self, *, tenant: str, file_name: str) -> DocumentRecord | None:
        query = (
            f"{_SELECT} WHERE tenant = %s AND file_name = %s AND is_latest "
            "ORDER BY created_at DESC, seq DESC LIMIT 1"
        )
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (tenant, file_name))
            row = cursor.fetchone()
        return _record_from_row(row) if row is not None else None
