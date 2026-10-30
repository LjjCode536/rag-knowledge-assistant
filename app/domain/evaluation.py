"""评估领域:黄金数据集契约与检索指标(教材第 44 章的生产版)。

检索指标只依赖"预期切片 / 实际命中"的编号集合,不依赖生成模型;
因此无密钥环境(CI)也能复现。指标口径在此唯一定义,报告与 CI 门禁共用。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.domain.lexical import text_tokens
from app.domain.retrieval import ChunkFilter


class EvaluationCategory(StrEnum):
    """评估品类:分品类看指标,避免均值掩盖弱项(教材第 44 章)。"""

    REFUND = "refund"
    LOGISTICS = "logistics"
    INVOICE = "invoice"
    LEAVE = "leave"
    REIMBURSEMENT = "reimbursement"
    IT = "it"
    SECURITY = "security"
    PRODUCT = "product"
    HR = "hr"
    UNKNOWN = "unknown"
    PERMISSION = "permission"


class EvaluationDifficulty(StrEnum):
    """用例难度:便于在报告里区分基础问题与边界问题。"""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ExpectedChunk(BaseModel):
    """预期切片定位:文档名 + 文内唯一锚点。

    黄金数据集不能写随机 uuid(每次入库都会变),也不能只写切片序号
    (切分参数一改就漂移)。锚点必须在该文档中恰好命中一个切片,
    评估器在索引完成后把它解析成真实 chunk_id,保证可追溯。
    """

    document: str = Field(min_length=1)
    anchor: str = Field(min_length=1)


class CorpusDocument(BaseModel):
    """评估语料文档:仓库内相对路径 + 入库时携带的权限元数据。"""

    path: str = Field(min_length=1)
    tenant: str = "default"
    department: str | None = None
    doc_type: str | None = None
    version: str = "v1"

    @property
    def file_name(self) -> str:
        return Path(self.path).name


class GoldenCase(BaseModel):
    """黄金用例:问题、品类、预期切片 / 关键词、权限过滤与拒答期望。"""

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    category: EvaluationCategory
    expected_chunks: list[ExpectedChunk] = Field(default_factory=list)
    expected_answer_keywords: list[str] = Field(default_factory=list)
    forbidden_documents: list[str] = Field(default_factory=list)
    filters: ChunkFilter = Field(default_factory=ChunkFilter)
    difficulty: EvaluationDifficulty = EvaluationDifficulty.EASY
    should_refuse: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def _expected_chunks_match_refusal(self) -> GoldenCase:
        if self.should_refuse and self.expected_chunks:
            raise ValueError("拒答用例不应声明预期切片")
        if not self.should_refuse and not self.expected_chunks:
            raise ValueError("可回答用例必须声明预期切片")
        pairs = [(item.document, item.anchor) for item in self.expected_chunks]
        if len(set(pairs)) != len(pairs):
            raise ValueError("同一用例的预期切片锚点不能重复")
        return self


class GoldenDataset(BaseModel):
    """版本化黄金数据集:语料清单 + 用例清单。"""

    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    corpus: list[CorpusDocument] = Field(min_length=1)
    cases: list[GoldenCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _identifiers_are_unique(self) -> GoldenDataset:
        case_ids = [case.id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("黄金数据集的用例 ID 必须唯一")
        file_names = [document.file_name for document in self.corpus]
        if len(set(file_names)) != len(file_names):
            raise ValueError("黄金数据集的语料文件名必须唯一")
        known = set(file_names)
        for case in self.cases:
            for expected in case.expected_chunks:
                if expected.document not in known:
                    raise ValueError(
                        f"用例 {case.id} 引用了语料清单中不存在的文档:{expected.document}"
                    )
            for document in case.forbidden_documents:
                if document not in known:
                    raise ValueError(
                        f"用例 {case.id} 禁读了语料清单中不存在的文档:{document}"
                    )
        return self


class ChunkLocator(BaseModel):
    """切片的稳定定位:文档名 + 文档内序号;跨运行对比不依赖随机 uuid。"""

    document: str
    index: int

    @property
    def key(self) -> str:
        return f"{self.document}#{self.index}"


class ExpectedChunkRef(ChunkLocator):
    """解析后的预期切片:锚点 + 文档内序号,用于报告逐条追溯。

    ``chunk_id`` 只供本次运行内部计算,序列化时排除:它每次入库都会变,
    写进报告会让跨运行的 diff 充满噪声。跨运行定位固定用「文档 + index + 锚点」。
    """

    anchor: str
    # JSON 报告刻意不写 chunk_id(每次入库都会变);反序列化时补空占位即可
    chunk_id: str = Field(default="", exclude=True)
    title: str | None = None


class RetrievedChunk(ChunkLocator):
    """一次检索命中:排名、文档内序号、文档与分数。"""

    rank: int = Field(ge=1)
    chunk_id: str = Field(default="", exclude=True)
    title: str | None = None
    score: float


class GenerationMetrics(BaseModel):
    """一组用例的生成指标(T09,教材第 44 章):忠实度 / 引用准确率 / 拒答正确率。

    - ``refusal_correctness`` 是**双向**判定:``refused == should_refuse`` 的
      用例比例——应拒答不被乱答、应回答不被误拒,两个方向都算一次判定;
    - ``faithfulness`` / ``citation_accuracy`` 只统计真正作答的用例,
      拒答(含错误拒答)没有回答内容,不进入这两个均值。
    """

    cases_total: int = 0
    answered: int = 0
    wrongly_refused: int = 0
    answer_rate: float | None = None
    faithfulness: float | None = None
    citation_accuracy: float | None = None
    refusal_correctness: float | None = None


class CaseResult(BaseModel):
    """单条用例的评估结果:预期、实际命中与逐指标,一一对应。"""

    case_id: str
    question: str
    category: EvaluationCategory
    difficulty: EvaluationDifficulty
    should_refuse: bool
    filters: dict[str, str] = Field(default_factory=dict)
    expected: list[ExpectedChunkRef] = Field(default_factory=list)
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    recall_at_k: float | None = None
    mrr: float | None = None
    ndcg_at_k: float | None = None
    hit_rate: float | None = None
    refusal_ok: bool | None = None
    permission_ok: bool | None = None
    forbidden_best_rank: int | None = None
    # T09 生成链路留痕:经 /api/v1/chat 的实际行为与生成指标
    refused: bool | None = None
    refusal_reason: str | None = None
    answer: str | None = None
    faithfulness: float | None = None
    citation_accuracy: float | None = None
    unsupported_claims: list[str] = Field(default_factory=list)
    citations: list[ChunkLocator] = Field(default_factory=list)
    notes: str = ""


class RetrievalMetrics(BaseModel):
    """一组用例的检索指标均值。"""

    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    hit_rate: float
    cases: int = Field(ge=0)


def _round(value: float) -> float:
    return round(value, 6)


class EvaluationReport(BaseModel):
    """一次评估的完整报告:配置 + 汇总 + 分品类 + 逐用例明细。"""

    schema_version: int = 1
    run_name: str
    dataset_name: str
    dataset_version: int
    k: int = Field(ge=1)
    top_k: int = Field(ge=1)
    min_score: float
    chunk_strategy: str
    chunk_size: int = Field(ge=1)
    chunk_overlap: int = Field(ge=0)
    embedder: str
    mode: str
    command: str
    # T12 起记录向量库后端(memory / chroma);旧报告无此字段,缺省 None
    vector_store: str | None = None
    # M4 起记录高级检索开关快照(混合 / MMR / 重排等),保证组合可追溯
    retrieval_config: dict[str, Any] | None = None
    cases_total: int = Field(ge=0)
    summary: RetrievalMetrics
    categories: dict[str, RetrievalMetrics] = Field(default_factory=dict)
    # 全部用例数(含拒答);categories 里的 cases 只统计可回答用例
    category_case_counts: dict[str, int] = Field(default_factory=dict)
    refusal_accuracy: float | None = None
    permission_pass_rate: float | None = None
    # T09 生成指标:同一轮评估经 /api/v1/chat 驱动生成链路后产出
    generator: str | None = None
    generation: GenerationMetrics | None = None
    generation_categories: dict[str, GenerationMetrics] = Field(default_factory=dict)
    case_results: list[CaseResult] = Field(default_factory=list)


def _expected_and_top_k(
    expected: Iterable[str], retrieved: Sequence[str], *, k: int
) -> tuple[set[str], list[str]]:
    """返回去重后的预期集合与实际 top-k 命中;k 非正时两者都为空。"""
    if k <= 0:
        return set(), []
    return set(expected), list(retrieved[:k])


def recall_at_k(expected: Iterable[str], retrieved: Sequence[str], *, k: int) -> float:
    """Recall@K:top-K 中命中的预期切片数 / 全部预期切片数。

    预期为空(拒答用例)不属于检索指标口径,返回 0;调用方应把拒答用例
    从检索均值中排除,而不是把它当成一次"全漏召回"。
    """
    expected_set, top = _expected_and_top_k(expected, retrieved, k=k)
    if not expected_set:
        return 0.0
    return len(expected_set & set(top)) / len(expected_set)


def hit_rate_at_k(expected: Iterable[str], retrieved: Sequence[str], *, k: int) -> float:
    """HitRate@K:top-K 中至少命中一条预期切片记 1,否则 0。"""
    expected_set, top = _expected_and_top_k(expected, retrieved, k=k)
    if not expected_set:
        return 0.0
    return 1.0 if expected_set & set(top) else 0.0


def mrr(expected: Iterable[str], retrieved: Sequence[str], *, k: int) -> float:
    """MRR@K:第一条预期切片排名的倒数;top-K 内没有命中记 0。"""
    expected_set, top = _expected_and_top_k(expected, retrieved, k=k)
    if not expected_set:
        return 0.0
    for rank, chunk_id in enumerate(top, start=1):
        if chunk_id in expected_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(expected: Iterable[str], retrieved: Sequence[str], *, k: int) -> float:
    """NDCG@K:二元相关性(命中=1)下的排序质量,除以理想排序的 DCG。"""
    expected_set, top = _expected_and_top_k(expected, retrieved, k=k)
    if not expected_set or not top:
        return 0.0

    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, chunk_id in enumerate(top, start=1)
        if chunk_id in expected_set
    )
    # 理想排序按 K 个位置计算,而不是按"实际返回条数":少返回结果本身就该被罚分。
    ideal_hits = min(len(expected_set), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


# ===== 生成指标:规则法(教材第 44 章;LLM-as-Judge 留作后续可选升级)=====
#
# 口径唯一定义在此,评估服务、报告与 CI 门禁共用同一把尺子:
# - 忠实度:回答的每个分句是否能在**所引资料**里找到词面支撑;资料外的
#   高风险断言(保证 / 立即到账类过度承诺)额外扣分并留痕;
# - 引用准确率:回答给出的引用中,确实支撑回答内容的比例。
# 两者都基于 `text_tokens` 的词面口径(与确定性替身 / BM25 通道同一分词),
# 不触网、不计费,无密钥环境(CI)可复现。

# 高风险断言:回答里出现而资料没有 → 一条扣 0.2(教材"过度承诺"清单)。
_RISKY_CLAIMS: tuple[str, ...] = (
    "保证",
    "一定",
    "百分百",
    "百分之百",
    "立即到账",
    "无需审核",
    "稳过",
)

# 回答模板里的套话:出现在几乎所有回答里,无判别力,计分前剥离。
_BOILER_PHRASES: tuple[str, ...] = (
    "根据知识库资料",
    "根据当前资料",
    "根据知识库,资料",
    "以下是相关内容",
    "可以参考以下内容",
    "供您参考",
)
# 剩余片段中的纯功能词(无判别力),整句只有这类词时该分句不参与计分;
# 「据知 / 识库 / 库资」等是套话固定短语被二字滑窗切出的残片。
_BOILER_TERMS: frozenset[str] = frozenset(
    {
        "根据", "据知", "知识", "识库", "库资", "资料", "当前", "可以", "参考",
        "以下", "内容", "用户", "需要", "应该", "建议", "说明",
        "回答", "问题", "如果", "那么", "并且", "但是", "或者", "以及", "所以",
        "因此", "因为", "另外", "此外", "相关", "这样", "这样子", "注意",
    }
)

_CLAUSE_SPLIT = re.compile(r"[,。,。;;、;;!?!?\n\r]+")


class FaithfulnessResult(BaseModel):
    """一次忠实度判定:分数 + 资料之外的高风险断言清单(供报告追溯)。"""

    score: float = Field(ge=0.0, le=1.0)
    unsupported_claims: list[str] = Field(default_factory=list)


def content_terms(text: str) -> list[str]:
    """剥掉套话后的内容分句词面:套话整句剔除,纯功能词分句跳过。"""
    stripped = text
    for phrase in _BOILER_PHRASES:
        stripped = stripped.replace(phrase, "")
    clauses = [clause for clause in _CLAUSE_SPLIT.split(stripped) if clause.strip()]
    content: list[str] = []
    for clause in clauses:
        tokens = [token for token in text_tokens(clause) if token not in _BOILER_TERMS]
        if tokens:
            content.append(" ".join(tokens))
    return content


def rule_based_faithfulness(
    answer: str, cited_texts: Sequence[str]
) -> FaithfulnessResult:
    """规则法忠实度:回答分句在所引资料里的支撑率,过度承诺额外扣分。

    - 每个内容分句的重叠词面占比 ≥ 0.5 记为"被支撑";回答无内容分句 → 1.0;
    - 未引用任何内容就作答 → 0.0(无据回答,报告与门禁同一个口径);
    - 资料外的过度承诺每条扣 0.2 并写入 unsupported_claims。
    """
    context = "\n".join(cited_texts)
    if not context.strip():
        return FaithfulnessResult(score=0.0, unsupported_claims=[])

    clauses = content_terms(answer)
    context_tokens = set(text_tokens(context))
    supported = 0
    for clause in clauses:
        clause_terms = set(clause.split())
        if not clause_terms:
            continue
        overlap = clause_terms & context_tokens
        if len(overlap) / len(clause_terms) >= 0.5:
            supported += 1

    base = supported / len(clauses) if clauses else 1.0
    unsupported = [
        claim for claim in _RISKY_CLAIMS if claim in answer and claim not in context
    ]
    score = max(0.0, min(1.0, base - 0.2 * len(unsupported)))
    return FaithfulnessResult(score=_round(score), unsupported_claims=unsupported)


def rule_based_citation_accuracy(answer: str, cited_texts: Sequence[str]) -> float:
    """规则法引用准确率:确实支撑回答内容的引用占全部引用的比例。

    一条引用"支撑回答"要求它与回答有至少两个词面交集(单个二字词面
    可能是巧合);无引用或回答没有任何内容词面 → 0(引用本就无处可靠)。
    """
    if not cited_texts:
        return 0.0
    answer_terms = set(" ".join(content_terms(answer)).split()) or set(text_tokens(answer))
    if not answer_terms:
        return 0.0
    supported = sum(
        1
        for text in cited_texts
        if len(answer_terms & set(text_tokens(text))) >= 2
    )
    return _round(supported / len(cited_texts))

