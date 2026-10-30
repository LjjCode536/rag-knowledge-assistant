"""反馈领域:回答的有用 / 无用反馈与差评的黄金用例候选(教材第 45 章评估闭环的线上入口)。

本层只描述"一次反馈留下了什么":verdict、一句话说明,以及与查询日志的关联;
聚合(同问题归并为候选)与导出格式由服务层与 API 层负责(ADR-0001)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domain.qa import RefusalReason


class FeedbackVerdict(StrEnum):
    """反馈判定:稳定码,进反馈记录与差评统计。"""

    USEFUL = "useful"
    USELESS = "useless"


class FeedbackRecord(BaseModel):
    """一次回答反馈:verdict + 可选一句话说明,锁定到一条查询日志。

    每条反馈经 ``query_id`` 关联查询日志,并复制该日志的 ``trace_id``,
    让反馈在链路追踪里与原问答对齐;同一 query 重复提交按服务层 upsert 覆盖。
    """

    feedback_id: str = Field(default_factory=lambda: f"fb_{uuid4().hex[:12]}")
    query_id: str
    trace_id: str
    verdict: FeedbackVerdict
    # 一句话说明;未填或空白由服务层归一为 None
    comment: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeedbackOrigin(BaseModel):
    """一条差评的来源:差评落在哪次问答上,答案当时长什么样。"""

    query_id: str
    trace_id: str
    verdict: FeedbackVerdict
    refused: bool
    refusal_reason: RefusalReason | None = None
    # 当时命中来源的文档 id(按命中排名去重排序),让候选人群可人工复核
    top_documents: list[str] = Field(default_factory=list)
    comment: str | None = None
    created_at: datetime


class GoldenCaseCandidate(BaseModel):
    """黄金用例候选:同问题差评聚类出的待改进项,供人工整理进黄金数据集。

    ``id`` 由问题文本确定性生成,同一问题跨导出保持稳定;``failure``
    说明为什么差评:被拒答记 ``refusal:<原因>``,可回答但没用记
    ``answer_not_useful``,两者混合记 ``mixed``。
    """

    id: str
    question: str
    occurrences: int = Field(ge=1)
    failure: str
    first_seen: datetime
    last_seen: datetime
    comments: list[str] = Field(default_factory=list)
    origins: list[FeedbackOrigin] = Field(default_factory=list)


def failure_of(*, refused: bool, refusal_reason: RefusalReason | None) -> str:
    """差评归因:拒答记 ``refusal:<原因>``,可回答但没用记 ``answer_not_useful``。"""
    if refused and refusal_reason is not None:
        return f"refusal:{refusal_reason.value}"
    return "answer_not_useful"
