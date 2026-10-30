"""query_logs 表仓储:追加 / 按 id 取 / 时间倒序列表 / 计数,与内存基线同契约。

``list_recent`` 按 ``(created_at DESC, seq DESC)``:时间同微秒时按插入序,
保证与内存实现的"插入顺序即时间顺序"一致。
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.qa import (
    GenerationUsage,
    GeneratorKind,
    QueryHit,
    QueryLog,
    RefusalReason,
)
from app.repositories.postgres.base import PgDatabase, placeholder_sql

_COLUMNS = (
    "query_id, trace_id, question, answer, refused, refusal_reason, hits, filters, "
    "top_k, min_score, latency_ms, generator, token_usage, rewritten_question, "
    "expanded_questions, created_at"
)

_INSERT = (
    f"INSERT INTO query_logs ({_COLUMNS}) VALUES ({placeholder_sql(len(_COLUMNS.split(', ')))})"
)

_SELECT = f"SELECT {_COLUMNS} FROM query_logs"


def _row_values(log: QueryLog) -> tuple:
    return (
        log.query_id,
        log.trace_id,
        log.question,
        log.answer,
        log.refused,
        log.refusal_reason.value if log.refusal_reason else None,
        Jsonb([hit.model_dump(mode="json") for hit in log.hits]),
        Jsonb(dict(log.filters)),
        log.top_k,
        log.min_score,
        log.latency_ms,
        log.generator.value,
        Jsonb(log.token_usage.model_dump(mode="json")),
        log.rewritten_question,
        Jsonb(list(log.expanded_questions)),
        log.created_at,
    )


def _log_from_row(row: dict) -> QueryLog:
    return QueryLog(
        query_id=row["query_id"],
        trace_id=row["trace_id"],
        question=row["question"],
        answer=row["answer"],
        refused=row["refused"],
        refusal_reason=RefusalReason(row["refusal_reason"]) if row["refusal_reason"] else None,
        hits=[QueryHit.model_validate(hit) for hit in (row["hits"] or [])],
        filters=dict(row["filters"] or {}),
        top_k=row["top_k"],
        min_score=row["min_score"],
        latency_ms=row["latency_ms"],
        generator=GeneratorKind(row["generator"]),
        token_usage=GenerationUsage.model_validate(row["token_usage"] or {}),
        rewritten_question=row["rewritten_question"],
        expanded_questions=list(row["expanded_questions"] or []),
        created_at=row["created_at"],
    )


class PgQueryLogRepository:
    """query_logs 表的数据仓储;一次问答一条日志,时间倒序读取。"""

    def __init__(self, db: PgDatabase) -> None:
        self._db = db

    def add(self, log: QueryLog) -> None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(_INSERT, _row_values(log))

    def get(self, query_id: str) -> QueryLog | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"{_SELECT} WHERE query_id = %s", (query_id,))
            row = cursor.fetchone()
        return _log_from_row(row) if row is not None else None

    def list_recent(self, *, limit: int) -> list[QueryLog]:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"{_SELECT} ORDER BY created_at DESC, seq DESC LIMIT %s", (limit,)
            )
            rows = cursor.fetchall()
        return [_log_from_row(row) for row in rows]

    def count(self) -> int:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT count(*) AS total FROM query_logs")
            row = cursor.fetchone()
        return int(row["total"]) if row is not None else 0
