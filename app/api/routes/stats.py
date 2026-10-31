"""统计端点:/stats 一屏看清系统健康度,成本明细可按时间段查询(T16)。

- ``GET /api/v1/stats``:知识库规模 + 问答健康度(问答量 / 拒答率 / 平均延迟)
  + 估算成本 + 分桶时间序列;
- ``GET /api/v1/stats/cost``:同一时间段的 Token 用量与估算成本明细。

统计口径来自已持久化的查询日志:pgvector 模式读生产表,重启容器不丢;
memory 与 chroma 模式的数据仓储仍是进程内存实现,统计随进程失忆。
区间为闭区间起点 / 开区间终点;分桶粒度缺省取配置。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of
from app.domain.stats import (
    CostReport,
    CostSummary,
    KnowledgeBaseStats,
    QaStats,
    StatsBucket,
    StatsBucketKind,
    TokenUsageSummary,
)
from app.services.stats import StatsService

router = APIRouter(prefix="/api/v1/stats", tags=["system"])


class TokenUsageView(BaseModel):
    """Token 用量视图:prompt / completion / total。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    @classmethod
    def of(cls, usage: TokenUsageSummary) -> TokenUsageView:
        return cls(**usage.model_dump())


class CostView(BaseModel):
    """估算成本视图:分 prompt / completion 两段计价,合计为总成本。"""

    prompt_cost: float
    completion_cost: float
    total_cost: float
    currency: str

    @classmethod
    def of(cls, cost: CostSummary) -> CostView:
        return cls(**cost.model_dump())


class QaStatsView(BaseModel):
    """问答健康度视图:问答量、拒答率与平均延迟。"""

    query_count: int
    refused_count: int
    refusal_rate: float | None = None
    avg_latency_ms: float | None = None
    token_usage: TokenUsageView

    @classmethod
    def of(cls, qa: QaStats) -> QaStatsView:
        return cls(**{**qa.model_dump(), "token_usage": TokenUsageView.of(qa.token_usage)})


class StatsBucketView(BaseModel):
    """分桶视图:桶的起止时间 + 该桶的问答健康度。"""

    bucket_start: datetime
    bucket_end: datetime
    qa: QaStatsView

    @classmethod
    def of(cls, bucket: StatsBucket) -> StatsBucketView:
        return cls(**{**bucket.model_dump(), "qa": QaStatsView.of(bucket.qa)})


class KnowledgeBaseStatsView(BaseModel):
    """知识库统计视图:规模 + 问答健康度 + 成本 + 分桶序列 + 统计区间。"""

    document_count: int
    indexed_document_count: int
    chunk_count: int
    qa: QaStatsView
    cost: CostView
    buckets: list[StatsBucketView]
    start: datetime | None = None
    end: datetime | None = None
    bucket_kind: StatsBucketKind

    @classmethod
    def of(cls, stats: KnowledgeBaseStats) -> KnowledgeBaseStatsView:
        return cls(
            **{
                **stats.model_dump(exclude={"qa", "cost", "buckets"}),
                "qa": QaStatsView.of(stats.qa),
                "cost": CostView.of(stats.cost),
                "buckets": [StatsBucketView.of(bucket) for bucket in stats.buckets],
            }
        )


class CostSummaryView(BaseModel):
    """成本明细视图:Token 用量 + 估算成本 + 统计区间。"""

    token_usage: TokenUsageView
    cost: CostView
    start: datetime | None = None
    end: datetime | None = None

    @classmethod
    def of(cls, report: CostReport) -> CostSummaryView:
        return cls(
            **{
                **report.model_dump(exclude={"token_usage", "cost"}),
                "token_usage": TokenUsageView.of(report.token_usage),
                "cost": CostView.of(report.cost),
            }
        )


@router.get(
    "",
    response_model=ApiEnvelope[KnowledgeBaseStatsView],
    summary="运营统计:文档数 / 问答量 / 拒答率 / 平均延迟 / 估算成本",
)
def get_stats(
    request: Request,
    start: Annotated[datetime | None, Query(description="区间起点(含)")] = None,
    end: Annotated[datetime | None, Query(description="区间终点(不含)")] = None,
    bucket: Annotated[
        StatsBucketKind | None, Query(description="分桶粒度(缺省取配置)")
    ] = None,
) -> ApiEnvelope[KnowledgeBaseStatsView]:
    """一屏看清系统健康度:知识库规模 + 问答健康度 + 估算成本 + 分桶序列。

    区间起点不早于终点时返回 400 `BAD_REQUEST`(判定在服务层,口径唯一);
    分桶粒度缺省取配置(`STATS_DEFAULT_BUCKET`)。
    """
    service: StatsService = request.app.state.stats_service
    stats = service.knowledge_base_stats(start=start, end=end, bucket=bucket)
    return success_envelope(
        KnowledgeBaseStatsView.of(stats), trace_id=trace_id_of(request)
    )


@router.get(
    "/cost",
    response_model=ApiEnvelope[CostSummaryView],
    summary="成本统计:Token 用量与估算成本(按时间段查询)",
)
def get_cost(
    request: Request,
    start: Annotated[datetime | None, Query(description="区间起点(含)")] = None,
    end: Annotated[datetime | None, Query(description="区间终点(不含)")] = None,
) -> ApiEnvelope[CostSummaryView]:
    """Token 用量按配置单价折算的估算成本;不含上游账单口径。

    成本是**区间汇总值**,不按桶拆分 —— 分桶序列在 `/stats` 里,这里只给
    一条时间段的用量与金额,避免同一个"bucket"参数在两个端点含义不同。
    """
    service: StatsService = request.app.state.stats_service
    report = service.cost_report(start=start, end=end)
    return success_envelope(CostSummaryView.of(report), trace_id=trace_id_of(request))
