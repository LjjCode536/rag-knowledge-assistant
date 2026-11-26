"""document_grants 表仓储:与内存实现同契约(ADR-0021)。

授权以逻辑文档(租户 + 文件名)为键;唯一索引保证同主体重复授权幂等。
仓储只做存取,权限判断在文档授权服务。
"""

from __future__ import annotations

from psycopg import errors as pg_errors
from psycopg.rows import dict_row

from app.domain.grants import DocumentGrant
from app.repositories.postgres.base import PgDatabase
from app.repositories.postgres.rows import document_grant_from_row

_COLUMNS = (
    "grant_id, tenant, file_name, subject_type, subject_id, granted_by, created_at"
)
_INSERT = (
    f"INSERT INTO document_grants ({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (tenant, file_name, subject_type, (COALESCE(subject_id, ''))) "
    "DO UPDATE SET granted_by = EXCLUDED.granted_by, created_at = EXCLUDED.created_at "
    "RETURNING grant_id"
)
_SELECT = f"SELECT {_COLUMNS} FROM document_grants"


def _values(grant: DocumentGrant) -> tuple:
    return (
        grant.grant_id,
        grant.tenant,
        grant.file_name,
        grant.subject_type.value,
        grant.subject_id,
        grant.granted_by,
        grant.created_at,
    )


class PgDocumentGrantRepository:
    """document_grants 表的数据仓储。"""

    def __init__(self, db: PgDatabase) -> None:
        self._db = db

    def add(self, grant: DocumentGrant) -> None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            try:
                cursor.execute(_INSERT, _values(grant))
            except pg_errors.UniqueViolation as exc:
                # 主键碰撞只可能来自同一 grant_id 重复写入:按程序错误上抛
                raise ValueError(f"文档授权已存在:{grant.grant_id}") from exc

    def get(self, grant_id: str) -> DocumentGrant | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"{_SELECT} WHERE grant_id = %s", (grant_id,))
            row = cursor.fetchone()
        return document_grant_from_row(row) if row is not None else None

    def find(
        self,
        *,
        tenant: str,
        file_name: str,
        subject_type: str,
        subject_id: str | None,
    ) -> DocumentGrant | None:
        query = (
            f"{_SELECT} WHERE tenant = %s AND file_name = %s "
            "AND subject_type = %s AND subject_id IS NOT DISTINCT FROM %s LIMIT 1"
        )
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (tenant, file_name, subject_type, subject_id))
            row = cursor.fetchone()
        return document_grant_from_row(row) if row is not None else None

    def list_for_logical(self, *, tenant: str, file_name: str) -> list[DocumentGrant]:
        query = (
            f"{_SELECT} WHERE tenant = %s AND file_name = %s ORDER BY created_at, grant_id"
        )
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (tenant, file_name))
            rows = cursor.fetchall()
        return [document_grant_from_row(row) for row in rows]

    def list_relevant(
        self, *, tenant: str, principal_id: str, department: str | None
    ) -> list[DocumentGrant]:
        query = (
            f"{_SELECT} WHERE tenant = %s AND (subject_type = 'tenant' "
            "OR (subject_type = 'user' AND subject_id = %s) "
            "OR (subject_type = 'department' AND subject_id = %s))"
        )
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (tenant, principal_id, department))
            rows = cursor.fetchall()
        return [document_grant_from_row(row) for row in rows]

    def delete(self, grant_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute("DELETE FROM document_grants WHERE grant_id = %s", (grant_id,))

    def delete_for_logical(self, *, tenant: str, file_name: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "DELETE FROM document_grants WHERE tenant = %s AND file_name = %s",
                (tenant, file_name),
            )
