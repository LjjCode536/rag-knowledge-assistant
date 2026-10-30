"""evaluation_runs 表仓储:一次评估运行的留档(六张表之一,T13)。

评估报告照旧落 docs/iterations 的 Markdown + JSON 文件;本仓储是同一份
报告在生产库的留档面,供跨迭代追溯与门禁审计。
"""

from __future__ import annotations

from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.evaluation import EvaluationReport
from app.repositories.postgres.base import PgDatabase, placeholder_sql

_COLUMNS = (
    "run_id, run_name, dataset_name, dataset_version, embedder, mode, "
    "vector_store, command, summary, report"
)

_INSERT = (
    f"INSERT INTO evaluation_runs ({_COLUMNS}) "
    f"VALUES ({placeholder_sql(len(_COLUMNS.split(', ')))})"
)


def _row_values(report: EvaluationReport, run_id: str) -> tuple:
    return (
        run_id,
        report.run_name,
        report.dataset_name,
        report.dataset_version,
        report.embedder,
        report.mode,
        report.vector_store,
        report.command,
        Jsonb(report.summary.model_dump(mode="json")),
        Jsonb(report.model_dump(mode="json")),
    )


class PgEvaluationRunRepository:
    """evaluation_runs 表的数据仓储;run_id 主键,重复落库为程序错误。"""

    def __init__(self, db: PgDatabase) -> None:
        self._db = db

    def add(self, report: EvaluationReport, *, run_id: str) -> None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            try:
                cursor.execute(_INSERT, _row_values(report, run_id))
            except pg_errors.UniqueViolation as exc:
                raise ValueError(f"评估运行已存在:{run_id}") from exc

    def count(self) -> int:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT count(*) AS total FROM evaluation_runs")
            row = cursor.fetchone()
        return int(row["total"]) if row is not None else 0
