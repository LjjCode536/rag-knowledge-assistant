"""窄接缝:领域层余弦相似度的口径(所有向量库后端同源)。

余弦是三个向量库后端(内存 / Chroma / pgvector)的共同打分口径:
长度不一致、含零向量按 0 分处理,不参与有效排序。
"""

from __future__ import annotations

import pytest

from app.domain.retrieval import cosine_similarity


def test_identical_direction_scores_one():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_opposite_direction_scores_minus_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_orthogonal_vectors_score_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_scale_does_not_change_score():
    """向量长度不影响余弦:只是方向相关。"""
    assert cosine_similarity([1.0, 2.0], [3.0, 6.0]) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ([], []),
        ([1.0], []),
        ([1.0, 2.0], [1.0]),
        ([0.0, 0.0], [1.0, 2.0]),
        ([1.0, 2.0], [0.0, 0.0]),
    ],
)
def test_degenerate_inputs_score_zero(a, b):
    assert cosine_similarity(a, b) == 0.0


def test_scores_are_symmetric():
    assert cosine_similarity([0.3, 0.9], [0.7, 0.1]) == pytest.approx(
        cosine_similarity([0.7, 0.1], [0.3, 0.9])
    )
