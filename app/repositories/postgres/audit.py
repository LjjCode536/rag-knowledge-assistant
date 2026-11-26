"""audit_events 表仓储:只追加,按租户 / 动作 / 时间倒序读取(ADR-0020)。"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.audit import AuditAction, AuditEvent
from app.repositories.postgres.base import PgDatabase
from app.repositories.postgres.rows import audit_event_from_row

_COLUMNS = (
    "event_id, occurred_at, tenant, principal_id, action, resource_type, "
    "resource_id, result, trace_id, details"
)
_INSERT = (
    f"INSERT INTO audit_events ({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_SELECT = f"SELECT {_COLUMNS} FROM audit_events"


def _values(event: AuditEvent) -> tuple:
    return (
        event.event_id,
        event.occurred_at,
        event.tenant,
        event.principal_id,
        event.action.value,
        event.resource_type,
        event.resource_id,
        event.result.value,
        event.trace_id,
        Jsonb(dict(event.details)),
    )


def _filter_clause(
    *,
    tenant: str,
    actions: list[AuditAction] | None,
    start: datetime | None,
    end: datetime | None,
) -> tuple[str, list]:
    clauses = ["tenant = %s"]
    params: list = [tenant]
    if actions:
        clauses.append("action = ANY(%s::text[])")
        params.append([action.value for action in actions])
    if start is not None:
        clauses.append("occurred_at >= %s")
        params.append(start)
    if end is not None:
        clauses.append("occurred_at < %s")
        params.append(end)
    return " AND ".join(clauses), params


class PgAuditRepository:
    """audit_events 表的数据仓储;审计写入失败直接上抛。"""

    def __init__(self, db: PgDatabase) -> None:
        self._db = db

    def add(self, event: AuditEvent) -> None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(_INSERT, _values(event))

    def list(
        self,
        *,
        tenant: str,
        actions: list[AuditAction] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        where, params = _filter_clause(
            tenant=tenant, actions=actions, start=start, end=end
        )
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"{_SELECT} WHERE {where} "
                "ORDER BY occurred_at DESC, seq DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            )
            rows = cursor.fetchall()
        return [audit_event_from_row(row) for row in rows]

    def count(
        self,
        *,
        tenant: str,
        actions: list[AuditAction] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        where, params = _filter_clause(
            tenant=tenant, actions=actions, start=start, end=end
        )
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"SELECT count(*) AS total FROM audit_events WHERE {where}", params
            )
            row = cursor.fetchone()
        return int(row["total"]) if row is not None else 0
