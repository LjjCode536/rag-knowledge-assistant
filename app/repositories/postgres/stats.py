"""统计聚合的 PostgreSQL 实现:在生产表上直接聚合,重启不丢(T16)。

与内存实现同契约:区间口径为闭区间起点 / 开区间终点,``None`` 表示不设界;
分桶按 UTC 自然小时 / 自然日切分,没有问答的桶不出现。

聚合全部下推 SQL(``count`` / ``avg`` / ``sum`` / ``date_trunc``),不在
Python 侧拉全量日志再算——查询日志是唯一统计来源,量级随线上流量增长。

Token 用量存在 ``query_logs.token_usage`` 的 jsonb 里,取值口径:
``COALESCE((token_usage->>'prompt_tokens')::bigint, 0)``;字段缺失或非数值
时按 0 计(与内存实现的 ``GenerationUsage`` 默认值一致)。

SQL 拼装收口在纯函数(:func:`build_range_clause` / :func:`build_bucket_query`)
里:SQL 文本与参数顺序可离线断言,不依赖数据库即能验证口径。
"""

from __future__ import annotations

from datetime import UTC, datetime

from psycopg.rows import dict_row

from app.domain.qa import GenerationUsage
from app.domain.stats import (
    BucketAggregate,
    StatsBucketKind,
    TokenUsageSummary,
    as_utc,
)
from app.repositories.postgres.base import PgDatabase

# jsonb 取值口径:键名与 :class:`~app.domain.qa.GenerationUsage` 的字段名同名。
# 键名由领域模型推导 —— 字段改名时 SQL 与写库侧一起变,不会静默读 0。
_USAGE_FIELDS = set(GenerationUsage.model_fields)


def _usage_tokens_sql(field: str) -> str:
    """单个 Token 计数列的 SQL 片段;字段名不在领域模型里即程序错误。"""
    if field not in _USAGE_FIELDS:
        raise ValueError(f"未知 Token 用量字段:{field}")
    return f"COALESCE((token_usage->>'{field}')::bigint, 0)"


_PROMPT_TOKENS = _usage_tokens_sql("prompt_tokens")
_COMPLETION_TOKENS = _usage_tokens_sql("completion_tokens")


def build_range_clause(
    start: datetime | None, end: datetime | None, *, params: list
) -> str:
    """区间 WHERE 片段:起点闭区间(含)/ 终点开区间(不含);无界则不加条件。

    参数按出现的顺序追加到 ``params``:先起点后终点,与 SQL 里的占位顺序一致。
    """
    clauses: list[str] = []
    if start is not None:
        clauses.append("created_at >= %s")
        params.append(as_utc(start))
    if end is not None:
        clauses.append("created_at < %s")
        params.append(as_utc(end))
    return " AND ".join(clauses) if clauses else "true"


def build_bucket_query(
    start: datetime | None,
    end: datetime | None,
    bucket: StatsBucketKind,
) -> tuple[str, list]:
    """分桶聚合 SQL 与参数。

    参数顺序:``date_trunc`` 的单位占位最先(在 SELECT 里),区间参数在后
    (在 WHERE 里)——占位与参数必须同序,否则过滤条件会错位。
    """
    params: list = []
    where = build_range_clause(start, end, params=params)
    sql = (
        "SELECT date_trunc(%s, created_at AT TIME ZONE 'UTC') AS bucket_start, "
        "count(*) AS query_count, "
        "count(*) FILTER (WHERE refused) AS refused_count, "
        "avg(latency_ms) AS avg_latency_ms, "
        f"coalesce(sum({_PROMPT_TOKENS}), 0) AS prompt_tokens, "
        f"coalesce(sum({_COMPLETION_TOKENS}), 0) AS completion_tokens "
        "FROM query_logs "
        f"WHERE {where} "
        "GROUP BY 1 ORDER BY 1 ASC"
    )
    return sql, [bucket.trunc_unit, *params]


def build_sum_query(start: datetime | None, end: datetime | None) -> tuple[str, list]:
    """Token 用量求和 SQL 与参数。"""
    params: list = []
    where = build_range_clause(start, end, params=params)
    sql = (
        "SELECT "
        f"coalesce(sum({_PROMPT_TOKENS}), 0) AS prompt_tokens, "
        f"coalesce(sum({_COMPLETION_TOKENS}), 0) AS completion_tokens "
        f"FROM query_logs WHERE {where}"
    )
    return sql, params


class PgStatsRepository:
    """生产库上的统计聚合:文档 / 切片规模 + 查询日志的问答与 Token 指标。

    全部聚合走 SQL 下推(``count`` / ``avg`` / ``sum`` / ``date_trunc``),
    不经其他仓储取数 —— 查询日志是唯一统计来源,量级随线上流量增长,
    全量拉取不可持续。三个仓储参数因此不接:统计面只依赖本连接池。
    """

    def __init__(self, *, db: PgDatabase) -> None:
        self._db = db

    def count_documents(self, *, latest_only: bool = True) -> int:
        where = "is_latest" if latest_only else "true"
        return int(self._scalar(f"SELECT count(*) AS total FROM documents WHERE {where}"))

    def count_indexed_documents(self, *, latest_only: bool = True) -> int:
        clauses = ["status = 'indexed'"]
        if latest_only:
            clauses.append("is_latest")
        where = " AND ".join(clauses)
        return int(
            self._scalar(f"SELECT count(*) AS total FROM documents WHERE {where}")
        )

    def count_chunks(self) -> int:
        return int(self._scalar("SELECT count(*) AS total FROM chunks"))

    def count_queries(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> int:
        params: list = []
        where = build_range_clause(start, end, params=params)
        return int(
            self._scalar(
                f"SELECT count(*) AS total FROM query_logs WHERE {where}", params
            )
        )

    def count_refusals(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> int:
        params: list = []
        where = build_range_clause(start, end, params=params)
        return int(
            self._scalar(
                f"SELECT count(*) AS total FROM query_logs WHERE {where} AND refused",
                params,
            )
        )

    def avg_latency_ms(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> float | None:
        params: list = []
        where = build_range_clause(start, end, params=params)
        value = self._scalar(
            f"SELECT avg(latency_ms) AS value FROM query_logs WHERE {where}", params
        )
        return None if value is None else round(float(value), 2)

    def sum_token_usage(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> TokenUsageSummary:
        sql, params = build_sum_query(start, end)
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        prompt = int(row["prompt_tokens"]) if row else 0
        completion = int(row["completion_tokens"]) if row else 0
        return TokenUsageSummary(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        )

    def bucket_queries(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        bucket: StatsBucketKind = StatsBucketKind.DAY,
    ) -> list[BucketAggregate]:
        """按自然小时 / 自然日切桶聚合;没有问答的桶不出现(不补零桶)。"""
        sql, params = build_bucket_query(start, end, bucket)
        delta = bucket.delta
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        aggregates = []
        for row in rows:
            bucket_start = row["bucket_start"]
            if bucket_start.tzinfo is None:
                bucket_start = bucket_start.replace(tzinfo=UTC)
            prompt = int(row["prompt_tokens"])
            completion = int(row["completion_tokens"])
            aggregates.append(
                BucketAggregate(
                    bucket_start=bucket_start,
                    bucket_end=bucket_start + delta,
                    query_count=int(row["query_count"]),
                    refused_count=int(row["refused_count"]),
                    avg_latency_ms=(
                        None
                        if row["avg_latency_ms"] is None
                        else round(float(row["avg_latency_ms"]), 2)
                    ),
                    token_usage=TokenUsageSummary(
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                        total_tokens=prompt + completion,
                    ),
                )
            )
        return aggregates

    def _scalar(self, sql: str, params: list | None = None) -> int | float | None:
        """执行聚合计值并取第一列;无结果行返回 None。"""
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params or [])
            row = cursor.fetchone()
        if row is None:
            return None
        return next(iter(row.values()))
