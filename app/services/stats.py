"""统计服务:把持久化的查询日志折算成运营指标与估算成本(T16)。

口径在本层收口:
- **拒答率** = 拒答数 / 问答量(无问答为 None,不伪造 0);
- **估算成本** = Token 用量 x 配置单价(每千 Token 元),分 prompt /
  completion 两段计价;这是**估算**,不是上游账单金额;
- **分桶**按自然小时 / 自然日切分,没有问答的桶不出现。

统计一律来自已持久化的查询日志,进程重启不丢(pgvector 模式)。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import Settings
from app.core.errors import BadRequestError
from app.domain.repositories import StatsRepository
from app.domain.stats import (
    BucketAggregate,
    CostReport,
    CostSummary,
    KnowledgeBaseStats,
    QaStats,
    StatsBucket,
    StatsBucketKind,
    TokenUsageSummary,
)


class StatsService:
    """运营统计与成本统计的用例层:核心指标一屏 + 成本明细。"""

    def __init__(self, *, repository: StatsRepository, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings

    @property
    def default_bucket(self) -> StatsBucketKind:
        """默认分桶粒度:由配置决定(``STATS_DEFAULT_BUCKET``)。"""
        return StatsBucketKind(self._settings.stats_default_bucket)

    def knowledge_base_stats(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        bucket: StatsBucketKind | None = None,
    ) -> KnowledgeBaseStats:
        """/stats 一屏:知识库规模 + 问答健康度 + 估算成本 + 分桶序列。

        ``bucket`` 缺省时用配置里的默认粒度(:attr:`default_bucket`)。
        """
        bucket = bucket or self.default_bucket
        start, end = _normalize_range(start, end)
        query_count = self._repository.count_queries(start=start, end=end)
        refused_count = self._repository.count_refusals(start=start, end=end)
        avg_latency = self._repository.avg_latency_ms(start=start, end=end)
        usage = self._repository.sum_token_usage(start=start, end=end)
        buckets = self._bucket_stats(start=start, end=end, bucket=bucket)
        return KnowledgeBaseStats(
            document_count=self._repository.count_documents(),
            indexed_document_count=self._repository.count_indexed_documents(),
            chunk_count=self._repository.count_chunks(),
            qa=QaStats(
                query_count=query_count,
                refused_count=refused_count,
                refusal_rate=_ratio(refused_count, query_count),
                avg_latency_ms=avg_latency,
                token_usage=usage,
            ),
            cost=self.estimate_cost(usage),
            buckets=buckets,
            start=start,
            end=end,
            bucket_kind=bucket,
        )

    def cost_summary(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> CostSummary:
        """成本明细:区间内的 Token 用量折算出的估算成本。"""
        return self.cost_report(start=start, end=end).cost

    def cost_report(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> CostReport:
        """成本报告:Token 用量 + 估算成本 + 统计区间(一次聚合出齐)。

        成本是区间汇总值,不按桶拆分 —— 分桶序列在
        :meth:`knowledge_base_stats` 里。
        """
        start, end = _normalize_range(start, end)
        usage = self._repository.sum_token_usage(start=start, end=end)
        return CostReport(
            token_usage=usage,
            cost=self.estimate_cost(usage),
            start=start,
            end=end,
        )

    def estimate_cost(self, usage: TokenUsageSummary) -> CostSummary:
        """按配置单价折算估算成本(每千 Token 元)。

        单价是配置项,默认按 ``Qwen/Qwen3-8B`` 量级给出占位值;换模型时
        只改配置,统计口径与代码无关。
        """
        prompt_price = self._settings.cost_prompt_price_per_1k
        completion_price = self._settings.cost_completion_price_per_1k
        prompt_cost = usage.prompt_tokens / 1000 * prompt_price
        completion_cost = usage.completion_tokens / 1000 * completion_price
        return CostSummary(
            prompt_cost=round(prompt_cost, 6),
            completion_cost=round(completion_cost, 6),
            total_cost=round(prompt_cost + completion_cost, 6),
            currency=self._settings.cost_currency,
        )

    def _bucket_stats(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        bucket: StatsBucketKind,
    ) -> list[StatsBucket]:
        """把仓储的分桶原始量折算成带拒答率的分桶指标。"""
        aggregates: list[BucketAggregate] = self._repository.bucket_queries(
            start=start, end=end, bucket=bucket
        )
        return [
            StatsBucket(
                bucket_start=aggregate.bucket_start,
                bucket_end=aggregate.bucket_end,
                qa=QaStats(
                    query_count=aggregate.query_count,
                    refused_count=aggregate.refused_count,
                    refusal_rate=_ratio(aggregate.refused_count, aggregate.query_count),
                    avg_latency_ms=aggregate.avg_latency_ms,
                    token_usage=aggregate.token_usage,
                ),
            )
            for aggregate in aggregates
        ]


def _ratio(numerator: int, denominator: int) -> float | None:
    """比率:分母为 0 时返回 None(没有样本 ≠ 比率 0)。"""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _normalize_range(
    start: datetime | None, end: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """区间口径归一:起点闭区间、终点开区间;时区缺失按 UTC 处理。

    起点不早于终点是**请求语义错误**,抛 :class:`BadRequestError`(400),
    不是 :class:`ValueError` —— 任何调用方(HTTP / CLI / 未来的 MCP 适配)
    都拿到同一个可映射到错误码的异常,不会落到 500。
    """
    normalized_start = _as_utc(start)
    normalized_end = _as_utc(end)
    if (
        normalized_start is not None
        and normalized_end is not None
        and normalized_start >= normalized_end
    ):
        raise BadRequestError(
            "统计区间起点必须早于终点",
            details={"start": normalized_start, "end": normalized_end},
        )
    return normalized_start, normalized_end


def _as_utc(value: datetime | None) -> datetime | None:
    """无时区的时间按 UTC 解释(避免与库里的 timestamptz 混比)。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
