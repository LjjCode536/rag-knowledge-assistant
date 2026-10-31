"""统计服务窄接缝:拒答率、延迟均值、Token 用量与估算成本的口径(T16)。

聚合的原始量来自内存统计仓储(与 PostgreSQL 实现同契约);本文件只验证
"服务层怎么把原始量折算成指标与成本",不碰 HTTP 与数据库。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.core.errors import BadRequestError
from app.domain.qa import (
    GenerationUsage,
    GeneratorKind,
    QueryLog,
)
from app.domain.stats import StatsBucketKind
from app.repositories.memory import (
    InMemoryChunkRepository,
    InMemoryDocumentRepository,
    InMemoryQueryLogRepository,
    InMemoryStatsRepository,
)
from app.services.stats import StatsService

_BASE = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)


def _log(
    *,
    minutes_ago: int,
    refused: bool = False,
    latency_ms: float = 10.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> QueryLog:
    """构造一条查询日志:时间相对 ``_BASE`` 回溯,便于断言分桶边界。"""
    return QueryLog(
        trace_id="trace-stats-0001",
        question="入职多久可以申请年假?",
        answer="根据当前资料无法确认。" if refused else "入职满一年可申请年假。",
        refused=refused,
        top_k=3,
        min_score=0.35,
        latency_ms=latency_ms,
        generator=GeneratorKind.LLM,
        token_usage=GenerationUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        created_at=_BASE - timedelta(minutes=minutes_ago),
    )


@pytest.fixture
def repository() -> InMemoryStatsRepository:
    return InMemoryStatsRepository(
        documents=InMemoryDocumentRepository(),
        chunks=InMemoryChunkRepository(),
        query_logs=InMemoryQueryLogRepository(),
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        siliconflow_api_key=None,
        cost_prompt_price_per_1k=0.002,
        cost_completion_price_per_1k=0.006,
    )


def test_stats_reports_refusal_rate_and_average_latency(repository, settings):
    repository._query_logs.add(_log(minutes_ago=0, latency_ms=10.0))
    repository._query_logs.add(_log(minutes_ago=1, latency_ms=20.0))
    repository._query_logs.add(_log(minutes_ago=2, refused=True, latency_ms=30.0))

    service = StatsService(repository=repository, settings=settings)
    stats = service.knowledge_base_stats()

    assert stats.qa.query_count == 3
    assert stats.qa.refused_count == 1
    assert stats.qa.refusal_rate == pytest.approx(1 / 3, abs=1e-6)
    assert stats.qa.avg_latency_ms == pytest.approx(20.0)


def test_stats_without_queries_reports_none_ratios(repository, settings):
    """没有样本不等于比率 0:拒答率与延迟均值都是 None。"""
    service = StatsService(repository=repository, settings=settings)

    stats = service.knowledge_base_stats()

    assert stats.qa.query_count == 0
    assert stats.qa.refusal_rate is None
    assert stats.qa.avg_latency_ms is None
    assert stats.cost.total_cost == 0.0
    assert stats.buckets == []


def test_cost_estimate_applies_configured_prices(repository, settings):
    """成本口径:Token 用量按配置单价(元 / 千 Token)折算。"""
    repository._query_logs.add(
        _log(minutes_ago=0, prompt_tokens=1000, completion_tokens=500)
    )

    service = StatsService(repository=repository, settings=settings)
    cost = service.cost_summary()

    # prompt 1000 token x 0.002 元/千 = 0.002;completion 500 x 0.006/千 = 0.003
    assert cost.prompt_cost == pytest.approx(0.002)
    assert cost.completion_cost == pytest.approx(0.003)
    assert cost.total_cost == pytest.approx(0.005)
    assert cost.currency == "CNY"


def test_stats_respects_time_range_boundaries(repository, settings):
    """区间口径:起点闭区间、终点开区间。"""
    repository._query_logs.add(_log(minutes_ago=0))  # _BASE(恰在终点上,不含)
    repository._query_logs.add(_log(minutes_ago=30))  # _BASE - 30min(恰在起点上,含)
    repository._query_logs.add(_log(minutes_ago=60))  # _BASE - 1h(起点之前,不含)

    service = StatsService(repository=repository, settings=settings)
    stats = service.knowledge_base_stats(start=_BASE - timedelta(minutes=30), end=_BASE)

    assert stats.qa.query_count == 1
    assert stats.start == _BASE - timedelta(minutes=30)
    assert stats.end == _BASE


def test_stats_rejects_inverted_time_range(repository, settings):
    """起点不早于终点是请求语义错误:抛 BadRequestError(可映射到 400)。"""
    service = StatsService(repository=repository, settings=settings)

    with pytest.raises(BadRequestError, match="起点必须早于终点"):
        service.knowledge_base_stats(start=_BASE, end=_BASE - timedelta(hours=1))


def test_stats_accepts_mixed_naive_and_aware_datetimes(repository, settings):
    """带时区 / 不带时区的时间混用:按 UTC 归一后比较,不抛 TypeError。"""
    repository._query_logs.add(_log(minutes_ago=0))
    service = StatsService(repository=repository, settings=settings)

    stats = service.knowledge_base_stats(
        start=datetime(2026, 10, 30, 7, 0), end=_BASE + timedelta(hours=1)
    )

    assert stats.qa.query_count == 1


def test_stats_buckets_split_by_hour(repository, settings):
    """小时桶按自然小时切;没有问答的桶不出现。"""
    repository._query_logs.add(_log(minutes_ago=0))  # 08:00
    repository._query_logs.add(_log(minutes_ago=10))  # 07:50
    repository._query_logs.add(_log(minutes_ago=70))  # 06:50

    service = StatsService(repository=repository, settings=settings)
    stats = service.knowledge_base_stats(bucket=StatsBucketKind.HOUR)

    starts = [bucket.bucket_start for bucket in stats.buckets]
    assert starts == [
        datetime(2026, 10, 30, 6, 0, tzinfo=UTC),
        datetime(2026, 10, 30, 7, 0, tzinfo=UTC),
        datetime(2026, 10, 30, 8, 0, tzinfo=UTC),
    ]
    assert [bucket.qa.query_count for bucket in stats.buckets] == [1, 1, 1]
    assert stats.bucket_kind == StatsBucketKind.HOUR


def test_stats_daily_bucket_groups_same_day_queries(repository, settings):
    """日桶按自然日切:同一天的问答合到一个桶。"""
    repository._query_logs.add(_log(minutes_ago=0))
    repository._query_logs.add(_log(minutes_ago=120))

    service = StatsService(repository=repository, settings=settings)
    stats = service.knowledge_base_stats(bucket=StatsBucketKind.DAY)

    assert len(stats.buckets) == 1
    assert stats.buckets[0].bucket_start == datetime(2026, 10, 30, 0, 0, tzinfo=UTC)
    assert stats.buckets[0].bucket_end == datetime(2026, 10, 31, 0, 0, tzinfo=UTC)
    assert stats.buckets[0].qa.query_count == 2
