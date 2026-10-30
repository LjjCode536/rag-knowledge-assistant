"""指标定义的纯接缝:检索指标与生成指标(教材第 44 章,规则法先行)。"""

from __future__ import annotations

import math

import pytest

from app.domain.evaluation import (
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    recall_at_k,
    rule_based_citation_accuracy,
    rule_based_faithfulness,
)


def test_recall_at_k_counts_distinct_expected_chunks_in_top_k() -> None:
    expected = ["a", "b", "c"]
    retrieved = ["x", "a", "y", "b", "c"]

    assert recall_at_k(expected, retrieved, k=5) == pytest.approx(1.0)
    assert recall_at_k(expected, retrieved, k=2) == pytest.approx(1 / 3)
    assert recall_at_k(expected, retrieved, k=3) == pytest.approx(1 / 3)


def test_recall_at_k_ignores_duplicate_retrieved_ids() -> None:
    assert recall_at_k(["a", "b"], ["a", "a", "x"], k=3) == pytest.approx(0.5)


def test_mrr_uses_first_relevant_rank() -> None:
    assert mrr(["a"], ["x", "y", "a"], k=3) == pytest.approx(1 / 3)
    assert mrr(["a"], ["a", "x"], k=3) == pytest.approx(1.0)
    assert mrr(["a"], ["x", "y"], k=3) == pytest.approx(0.0)


def test_ndcg_at_k_uses_binary_relevance_and_ideal_ranking() -> None:
    # 预期 2 条,检索到 1 条且排第 2 位:
    # DCG = 1/log2(3);IDCG = 1/log2(2) + 1/log2(3)
    expected = ["a", "b"]
    retrieved = ["x", "a", "b"]
    expected_value = (1 / math.log2(3) + 1 / math.log2(4)) / (
        1 / math.log2(2) + 1 / math.log2(3)
    )

    assert ndcg_at_k(expected, retrieved, k=3) == pytest.approx(expected_value)
    assert ndcg_at_k(expected, retrieved, k=2) == pytest.approx(
        (1 / math.log2(3)) / (1 / math.log2(2) + 1 / math.log2(3))
    )


def test_ndcg_at_k_penalizes_returning_fewer_than_k_results() -> None:
    # 预期 3 条、只返回 1 条且命中:DCG=1,IDCG=1+1/log2(3)+1/log2(4)
    expected_value = 1 / (1 + 1 / math.log2(3) + 1 / math.log2(4))

    assert ndcg_at_k(["a", "b", "c"], ["a"], k=3) == pytest.approx(expected_value)


def test_metrics_handle_empty_expectation_and_non_positive_k() -> None:
    assert recall_at_k([], ["a"], k=3) == 0.0
    assert mrr([], ["a"], k=3) == 0.0
    assert ndcg_at_k([], ["a"], k=3) == 0.0
    assert hit_rate_at_k([], ["a"], k=3) == 0.0
    assert recall_at_k(["a"], ["a"], k=0) == 0.0
    assert ndcg_at_k(["a"], ["a"], k=0) == 0.0


# ===== 生成指标:规则法(教材第 44 章 Faithfulness / Citation Accuracy)=====

# 教材样例语境:语料原文里确实说"尚未发货可退款"
_REFUND_CONTEXT = (
    "订单尚未发货时,客户可以申请取消订单并全额退款,退款将在三个工作日内原路退回。"
)


def test_faithfulness_accepts_paraphrase_of_cited_content() -> None:
    """忠实:回答只改写了资料原句 → 1.0(教材"回答是否编造"的正例)。"""
    result = rule_based_faithfulness(
        "根据知识库资料,订单未发货时,客户可以申请取消订单并退款。",
        [_REFUND_CONTEXT],
    )

    assert result.score == 1.0
    assert result.unsupported_claims == []


def test_faithfulness_penalizes_fabricated_detail() -> None:
    """编造:资料没说"10 分钟到账",含该断言的回答不得满分(教材反例)。"""
    result = rule_based_faithfulness(
        "订单未发货时可以申请退款,并且 10 分钟内到账。",
        [_REFUND_CONTEXT],
    )

    assert result.score < 1.0
    assert result.unsupported_claims == []


def test_faithfulness_worst_case_is_zero() -> None:
    """整句内容都出现在所引资料之外 → 忠实度 0。"""
    result = rule_based_faithfulness(
        "公司报销全部机票并提供免费机场接送服务。",
        [_REFUND_CONTEXT],
    )

    assert result.score == 0.0


def test_faithfulness_penalizes_overstated_claims() -> None:
    """过度承诺:出现资料之外的"保证 / 百分百 / 立即到账"等高风险断言 → 扣分并留痕。"""
    fabrications = rule_based_faithfulness(
        "订单未发货时客户可以申请取消订单并全额退款,平台保证百分百立即到账。",
        ["退货退款申请需要在签收商品后发起。"],
    )

    assert fabrications.score < 1.0
    for claim in ("保证", "百分百", "立即到账"):
        assert claim in fabrications.unsupported_claims


def test_faithfulness_requires_cited_text() -> None:
    """没引用任何内容就作答 = 无据回答,忠实度 0(报告与门禁共用该口径)。"""
    result = rule_based_faithfulness("订单未发货时可以申请退款。", [])

    assert result.score == 0.0


def test_citation_accuracy_counts_supporting_citations_only() -> None:
    """引用准确率 = 确实支撑回答的引用比例(与词汇表口径一致)。"""
    score = rule_based_citation_accuracy(
        "根据知识库资料,差旅报销需要提交发票、行程单和审批记录。",
        [
            "差旅报销需要提交发票、行程单和审批记录,并在十五个工作日内完成。",
            "企业发票需要填写公司名称和纳税人识别号。",
        ],
    )

    assert score == pytest.approx(0.5)


def test_citation_accuracy_without_citations_is_zero() -> None:
    assert rule_based_citation_accuracy("回答内容", []) == 0.0
