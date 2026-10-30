"""检索指标定义的纯接缝:Recall@K / MRR / NDCG@K / HitRate@K(教材第 44 章)。"""

from __future__ import annotations

import math

import pytest

from app.domain.evaluation import hit_rate_at_k, mrr, ndcg_at_k, recall_at_k


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
