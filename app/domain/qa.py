"""在线问答领域的答案、引用与查询日志模型(教材第 45 章问答链路的生产版)。

本层只描述"一次问答留下了什么":有据回答、来源引用与可追溯的查询日志;
检索、生成与日志持久化如何实现由服务层与基础设施层负责(ADR-0001)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

# 资料不足时的唯一拒答文案:所有拒答路径共用,避免同义漂移。
REFUSAL_ANSWER = "根据当前资料无法确认。"


class RefusalReason(StrEnum):
    """拒答原因:稳定码,供查询日志、评估与反馈闭环统计。"""

    NO_HITS = "no_hits"
    LOW_SCORE = "low_score"
    GENERATOR_REFUSED = "generator_refused"


class GeneratorKind(StrEnum):
    """回答生成器种类:稳定码,进查询日志与响应,让规则版 / 真实 LLM 的行为差异可观测。"""

    RULE_BASED = "rule_based"
    LLM = "llm"


class GenerationUsage(BaseModel):
    """一次回答生成步的 Token 用量;规则版生成器恒为零。"""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class Citation(BaseModel):
    """来源引用:指回一条切片,并补齐文档、标题与页码等定位信息。"""

    index: int = Field(ge=1)
    chunk_id: str
    document_id: str
    file_name: str | None = None
    title: str | None = None
    page_number: int | None = None
    score: float


class ContextSource(BaseModel):
    """进入上下文的一条资料:引用定位 + 切片原文。"""

    citation: Citation
    content: str


class AnswerContext(BaseModel):
    """生成器的输入:编号资料文本 + 结构化来源。

    ``text`` 是给生成器(规则版或真实 LLM)的编号资料;
    ``sources`` 保留结构化来源,供引用回显与规则生成器取原文。
    """

    text: str
    sources: list[ContextSource] = Field(default_factory=list)


class QueryHit(BaseModel):
    """一次问答的检索命中记录:排名、分数与是否真正进入回答证据。"""

    rank: int = Field(ge=1)
    chunk_id: str
    document_id: str
    title: str | None = None
    page_number: int | None = None
    score: float
    used: bool = False


class QueryLog(BaseModel):
    """查询日志:原始与改写 / 扩展后的问题、命中、答案、拒答原因、Token 用量与端到端延迟。"""

    query_id: str = Field(default_factory=lambda: f"qry_{uuid4().hex[:12]}")
    trace_id: str
    question: str
    answer: str
    refused: bool
    refusal_reason: RefusalReason | None = None
    hits: list[QueryHit] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    top_k: int
    min_score: float
    latency_ms: float
    # 生成器留痕:用了哪种生成器、消耗多少 Token(规则版为零)
    generator: GeneratorKind
    token_usage: GenerationUsage = Field(default_factory=GenerationUsage)
    # 实际用于检索的改写与扩展查询(未启用 / 未改写 → 原问题即口径,记 None / 空)
    rewritten_question: str | None = None
    expanded_questions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
