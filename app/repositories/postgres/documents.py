"""documents 表仓储:与内存基线(:class:`InMemoryDocumentRepository`)同契约。

- ``add`` 同 id 重复插入是程序错误 → ``ValueError``(内存实现同语义);
- ``save`` 按 id 覆盖式落库(状态机流转后的档案更新),不存在则新建,
  与内存 dict 赋值语义对齐;
- ``find_latest_by_file`` 只取 ``is_latest`` 且 ``(tenant, file_name)`` 命中的
  最新一条,created_at 同微秒按插入序(seq)兜底排序;
- ``list`` / ``count`` 按同一套筛选条件工作(T17),过滤口径与内存实现逐条对齐。

``list`` 的排序口径:``created_at`` 倒序,同刻按 ``id`` 倒序兜底 —— 与内存
实现一致,保证次序确定、不依赖插入序。
"""

from __future__ import annotations

from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.documents import DocumentFilter, DocumentRecord
from app.repositories.postgres.base import PgDatabase, placeholder_sql
from app.repositories.postgres.rows import document_from_row

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


def build_filter_clause(filters: DocumentFilter) -> tuple[str, list]:
    """清单端点的 WHERE 片段与参数(T17)。

    与内存实现(:func:`app.repositories.memory._match_documents`)逐条对齐:

    - ``latest_only`` → ``is_latest``;
    - 租户 / 状态 / 部门 / 文档类型精确匹配;
    - ``keyword`` → 文件名子串匹配,**大小写不敏感**(用 ``ILIKE``,
      通配符转义,避免用户输入的 ``%`` / ``_`` 被当成通配符);
    - 多个条件按"与"组合。

    参数按出现顺序追加到 ``params``,与 SQL 里的占位顺序一致。
    """
    clauses: list[str] = []
    params: list = []
    if filters.latest_only:
        clauses.append("is_latest")
    if filters.tenant is not None:
        clauses.append("tenant = %s")
        params.append(filters.tenant)
    if filters.status is not None:
        clauses.append("status = %s")
        params.append(filters.status.value)
    if filters.department is not None:
        clauses.append("department = %s")
        params.append(filters.department)
    if filters.doc_type is not None:
        clauses.append("doc_type = %s")
        params.append(filters.doc_type)
    keyword = filters.keyword.strip() if filters.keyword else ""
    if keyword:
        clauses.append("file_name ILIKE %s")
        params.append(f"%{_escape_like(keyword)}%")
    return (" AND ".join(clauses) if clauses else "true"), params


def _escape_like(value: str) -> str:
    """转义 LIKE / ILIKE 的通配符,让用户输入按字面量匹配。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    """行 → 档案:映射口径收口在 :mod:`app.repositories.postgres.rows`。"""
    return document_from_row(row)


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

    def list(self, filters: DocumentFilter) -> list[DocumentRecord]:
        """按筛选条件列出档案(T17);口径与内存实现一致。"""
        where, params = build_filter_clause(filters)
        query = (
            f"{_SELECT} WHERE {where} "
            "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
        )
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, [*params, filters.limit, filters.offset])
            rows = cursor.fetchall()
        return [_record_from_row(row) for row in rows]

    def count(self, filters: DocumentFilter) -> int:
        """符合筛选条件的档案总数(T17);与 list 同一套筛选条件。"""
        where, params = build_filter_clause(filters)
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"SELECT count(*) AS total FROM documents WHERE {where}", params)
            row = cursor.fetchone()
        return int(row["total"]) if row is not None else 0
