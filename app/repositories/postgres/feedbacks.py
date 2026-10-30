"""feedbacks 表仓储:一问一评同 id 覆盖(upsert),与内存基线同契约。

重复提交同一 ``feedback_id`` 按覆盖落库;``get_by_query_id`` 取该 query 的
现行反馈(created_at 同微秒按插入序兜底)。
"""

from __future__ import annotations

from psycopg.rows import dict_row

from app.domain.feedback import FeedbackRecord, FeedbackVerdict
from app.repositories.postgres.base import PgDatabase, placeholder_sql

_COLUMNS = "feedback_id, query_id, trace_id, verdict, comment, created_at"
_SET_COLUMNS = (
    "query_id = EXCLUDED.query_id, trace_id = EXCLUDED.trace_id, "
    "verdict = EXCLUDED.verdict, comment = EXCLUDED.comment, "
    "created_at = EXCLUDED.created_at"
)

_INSERT = (
    f"INSERT INTO feedbacks ({_COLUMNS}) VALUES ({placeholder_sql(len(_COLUMNS.split(', ')))})"
)

_UPSERT = f"{_INSERT} ON CONFLICT (feedback_id) DO UPDATE SET {_SET_COLUMNS}"

_SELECT = f"SELECT {_COLUMNS} FROM feedbacks"


def _row_values(record: FeedbackRecord) -> tuple:
    return (
        record.feedback_id,
        record.query_id,
        record.trace_id,
        record.verdict.value,
        record.comment,
        record.created_at,
    )


def _record_from_row(row: dict) -> FeedbackRecord:
    return FeedbackRecord(
        feedback_id=row["feedback_id"],
        query_id=row["query_id"],
        trace_id=row["trace_id"],
        verdict=FeedbackVerdict(row["verdict"]),
        comment=row["comment"],
        created_at=row["created_at"],
    )


class PgFeedbackRepository:
    """feedbacks 表的数据仓储;同 feedback_id 覆盖(服务层一问一评 upsert)。"""

    def __init__(self, db: PgDatabase) -> None:
        self._db = db

    def add(self, record: FeedbackRecord) -> None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(_UPSERT, _row_values(record))

    def get(self, feedback_id: str) -> FeedbackRecord | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"{_SELECT} WHERE feedback_id = %s", (feedback_id,))
            row = cursor.fetchone()
        return _record_from_row(row) if row is not None else None

    def get_by_query_id(self, query_id: str) -> FeedbackRecord | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"{_SELECT} WHERE query_id = %s ORDER BY created_at DESC, seq DESC LIMIT 1",
                (query_id,),
            )
            row = cursor.fetchone()
        return _record_from_row(row) if row is not None else None

    def list_recent(
        self, *, limit: int, verdict: FeedbackVerdict | None = None
    ) -> list[FeedbackRecord]:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            if verdict is None:
                cursor.execute(
                    f"{_SELECT} ORDER BY created_at DESC, seq DESC LIMIT %s", (limit,)
                )
            else:
                cursor.execute(
                    f"{_SELECT} WHERE verdict = %s ORDER BY created_at DESC, seq DESC LIMIT %s",
                    (verdict.value, limit),
                )
            rows = cursor.fetchall()
        return [_record_from_row(row) for row in rows]

    def count(self, *, verdict: FeedbackVerdict | None = None) -> int:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            if verdict is None:
                cursor.execute("SELECT count(*) AS total FROM feedbacks")
            else:
                cursor.execute(
                    "SELECT count(*) AS total FROM feedbacks WHERE verdict = %s",
                    (verdict.value,),
                )
            row = cursor.fetchone()
        return int(row["total"]) if row is not None else 0
