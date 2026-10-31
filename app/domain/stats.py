"""运营统计与成本统计的领域模型(教材第 45 章 stats 端点与成本统计升级项)。

本层只描述"统计留下了什么":知识库规模、问答健康度(拒答率 / 平均延迟)、
Token 用量与估算成本;聚合口径(时间区间、分桶)由服务层负责(ADR-0001)。

术语(见 GLOSSARY.md):
- **统计区间**:一次统计请求覆盖的时间范围(``start`` 闭区间 → ``end`` 开区间);
- **分桶**:把统计区间按固定粒度(hour / day)切成若干时间片,每片一份指标;
- **估算成本**:Token 用量按配置单价折算出的金额(不是上游账单金额)。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.qa import GenerationUsage


class StatsBucketKind(StrEnum):
    """分桶粒度:统计区间按自然小时 / 自然日切分。

    粒度与桶长的对应关系收口在枚举自身(:attr:`trunc_unit` / :attr:`delta`),
    内存实现与 PostgreSQL 实现共用同一份映射 —— 两处各写一份是口径漂移的起点。
    """

    HOUR = "hour"
    DAY = "day"

    @property
    def trunc_unit(self) -> str:
        """``date_trunc`` 的单位(小时 / 日)。"""
        return "hour" if self is StatsBucketKind.HOUR else "day"

    @property
    def delta(self) -> timedelta:
        """一个桶的时长。"""
        if self is StatsBucketKind.HOUR:
            return timedelta(hours=1)
        return timedelta(days=1)


class TokenUsageSummary(BaseModel):
    """Token 用量汇总:按 prompt / completion 分开计数,total 为二者之和。"""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: TokenUsageSummary) -> TokenUsageSummary:
        return TokenUsageSummary(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )

    @classmethod
    def of(cls, usage: GenerationUsage) -> TokenUsageSummary:
        """单次生成步的用量 → 汇总口径(结构相同,换类型)。"""
        return cls(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )

    def accumulate(self, usages: Iterable[GenerationUsage]) -> TokenUsageSummary:
        """把多次生成步的用量累加成本汇总(仓储聚合的共用口径)。"""
        total = TokenUsageSummary()
        for usage in usages:
            total = total + TokenUsageSummary.of(usage)
        return total


class CostSummary(BaseModel):
    """估算成本:Token 用量按配置单价折算的金额,单位为元。

    ``prompt_cost`` / ``completion_cost`` 分列,``total_cost`` 为二者之和;
    单价为"每千 Token 元",计价口径在配置里可见。
    """

    prompt_cost: float = Field(default=0.0, ge=0)
    completion_cost: float = Field(default=0.0, ge=0)
    total_cost: float = Field(default=0.0, ge=0)
    currency: str = "CNY"


class QaStats(BaseModel):
    """问答健康度:问答量、拒答率与延迟,按统计区间聚合。

    ``refusal_rate`` 为拒答数 / 问答量(无问答时为 None,不伪造 0);
    ``avg_latency_ms`` 为端到端延迟均值(无问答时为 None)。
    """

    query_count: int = Field(default=0, ge=0)
    refused_count: int = Field(default=0, ge=0)
    refusal_rate: float | None = None
    avg_latency_ms: float | None = None
    token_usage: TokenUsageSummary = Field(default_factory=TokenUsageSummary)


class StatsBucket(BaseModel):
    """一个时间桶的指标:桶的起点 / 终点 + 该桶内的问答与 Token 统计。"""

    bucket_start: datetime
    bucket_end: datetime
    qa: QaStats


class BucketAggregate(BaseModel):
    """仓储层的分桶聚合原始量:一个桶内的计数与均值。

    仓储只负责"把日志按桶切开并数清楚",拒答率与成本的折算由服务层做
    (服务层才知道单价与口径);因此这一层不带比率字段。
    """

    bucket_start: datetime
    bucket_end: datetime
    query_count: int = Field(default=0, ge=0)
    refused_count: int = Field(default=0, ge=0)
    avg_latency_ms: float | None = None
    token_usage: TokenUsageSummary = Field(default_factory=TokenUsageSummary)


def bucket_start_of(moment: datetime, bucket: StatsBucketKind) -> datetime:
    """桶起点:按 UTC 截断到自然小时 / 自然日。

    日桶跨时区口径固定为 UTC(查询日志的 ``created_at`` 统一存 UTC),
    避免同一份数据在不同机器上切出不同的桶。PostgreSQL 侧的
    ``date_trunc`` 与本函数同口径。
    """
    normalized = as_utc(moment)
    if bucket is StatsBucketKind.HOUR:
        return normalized.replace(minute=0, second=0, microsecond=0)
    return normalized.replace(hour=0, minute=0, second=0, microsecond=0)


def as_utc(value: datetime) -> datetime:
    """无时区的时间按 UTC 解释(避免与库里的 timestamptz 混比)。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CostReport(BaseModel):
    """成本报告:Token 用量 + 估算成本 + 统计区间(成本端点的完整口径)。

    成本是**区间汇总值**,不按桶拆分 —— 分桶序列在运营统计里。
    """

    token_usage: TokenUsageSummary = Field(default_factory=TokenUsageSummary)
    cost: CostSummary = Field(default_factory=CostSummary)
    start: datetime | None = None
    end: datetime | None = None


class KnowledgeBaseStats(BaseModel):
    """/stats 一屏看清系统健康度:知识库规模 + 问答健康度 + 成本。"""

    # 知识库规模
    document_count: int = Field(default=0, ge=0)
    indexed_document_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    # 问答健康度(统计区间内)
    qa: QaStats = Field(default_factory=QaStats)
    # 成本(统计区间内)
    cost: CostSummary = Field(default_factory=CostSummary)
    # 分桶时间序列(按 bucket_start 升序)
    buckets: list[StatsBucket] = Field(default_factory=list)
    # 统计区间(闭区间起点 / 开区间终点);None 表示"至今全部"
    start: datetime | None = None
    end: datetime | None = None
    bucket_kind: StatsBucketKind = StatsBucketKind.DAY
