"""principals 表仓储:种子账号的持久面(ADR-0020)。

服务启动只建空表,不建号;账号由 ``scripts/seed_principals.py`` 显式写入。
``upsert`` 按主体 id 幂等覆盖,重复执行种子脚本不产生重复账号。
"""

from __future__ import annotations

from psycopg.rows import dict_row

from app.domain.identity import PrincipalAccount
from app.repositories.postgres.base import PgDatabase, placeholder_sql
from app.repositories.postgres.rows import principal_from_row

_COLUMNS = (
    "principal_id, username, kind, tenant, role, department, display_name, password_hash"
)
_INSERT = (
    f"INSERT INTO principals ({_COLUMNS}) "
    f"VALUES ({placeholder_sql(len(_COLUMNS.split(', ')))})"
)
_UPSERT = (
    f"{_INSERT} ON CONFLICT (principal_id) DO UPDATE SET "
    "username = EXCLUDED.username, kind = EXCLUDED.kind, tenant = EXCLUDED.tenant, "
    "role = EXCLUDED.role, department = EXCLUDED.department, "
    "display_name = EXCLUDED.display_name, password_hash = EXCLUDED.password_hash"
)
_SELECT = f"SELECT {_COLUMNS} FROM principals"


def _row_values(account: PrincipalAccount) -> tuple:
    principal = account.principal
    return (
        principal.id,
        principal.username,
        principal.kind.value,
        principal.tenant,
        principal.role.value,
        principal.department,
        principal.display_name,
        account.password_hash,
    )


class PgPrincipalRepository:
    """principals 表的数据仓储;与内存实现同一契约。"""

    def __init__(self, db: PgDatabase) -> None:
        self._db = db

    def find_by_username(self, username: str) -> PrincipalAccount | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"{_SELECT} WHERE username = %s", (username,))
            row = cursor.fetchone()
        return principal_from_row(row) if row is not None else None

    def get(self, principal_id: str) -> PrincipalAccount | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"{_SELECT} WHERE principal_id = %s", (principal_id,))
            row = cursor.fetchone()
        return principal_from_row(row) if row is not None else None

    def upsert(self, account: PrincipalAccount) -> None:
        with self._db.connection() as conn, conn.cursor() as cursor:
            cursor.execute(_UPSERT, _row_values(account))

    def list(self) -> list[PrincipalAccount]:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"{_SELECT} ORDER BY principal_id")
            rows = cursor.fetchall()
        return [principal_from_row(row) for row in rows]
