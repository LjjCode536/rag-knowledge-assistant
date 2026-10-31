"""统计 SQL 拼装的离线断言:区间口径与占位 / 参数顺序(T16)。

真库不可达时(无 Docker)本文件仍能守住最容易出错的两处:
- **区间口径**:起点闭区间 / 终点开区间,缺哪一侧都不加条件;
- **占位与参数同序**:``date_trunc`` 的单位占位在 SELECT 里、���间占位在
  WHERE 里,两者顺序错位会让过滤条件作用到错误的字段上。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.stats import StatsBucketKind
from app.repositories.postgres.stats import (
    build_bucket_query,
    build_range_clause,
    build_sum_query,
)

_BASE = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)


def test_range_clause_without_bounds_has_no_condition():
    params: list = []

    clause = build_range_clause(None, None, params=params)

    assert clause == "true"
    assert params == []


def test_range_clause_uses_closed_start_and_open_end():
    params: list = []

    clause = build_range_clause(_BASE, _BASE.replace(hour=9), params=params)

    assert clause == "created_at >= %s AND created_at < %s"
    assert params == [_BASE, _BASE.replace(hour=9)]


def test_range_clause_normalizes_naive_datetime_to_utc():
    params: list = []

    build_range_clause(datetime(2026, 10, 30, 8, 0), None, params=params)

    assert params == [datetime(2026, 10, 30, 8, 0, tzinfo=UTC)]


def test_bucket_query_places_unit_placeholder_before_range_params():
    """占位顺序:单位(SELECT) → 起点 → 终点(WHERE);错位会让区间条件落空。"""
    sql, params = build_bucket_query(
        _BASE, _BASE.replace(hour=9), StatsBucketKind.HOUR
    )

    assert params == ["hour", _BASE, _BASE.replace(hour=9)]
    assert sql.index("%s") < sql.index("created_at >=")
    assert "date_trunc(%s" in sql
    assert "GROUP BY 1 ORDER BY 1 ASC" in sql
    assert "count(*) FILTER (WHERE refused)" in sql


def test_bucket_query_uses_day_unit_for_day_bucket():
    sql, params = build_bucket_query(None, None, StatsBucketKind.DAY)

    assert params == ["day"]
    assert "created_at" not in sql.split("WHERE")[1]


def test_sum_query_has_no_params_without_bounds():
    sql, params = build_sum_query(None, None)

    assert params == []
    assert "coalesce(sum(" in sql
    assert "token_usage->>'prompt_tokens'" in sql
    assert "token_usage->>'completion_tokens'" in sql
