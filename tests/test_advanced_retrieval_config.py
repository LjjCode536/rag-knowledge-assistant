"""高级检索配置缝:逐项开关与融合参数(教材第 43 章 RetrievalConfig 模式)。

验收口径:
- 各能力经配置逐项开关;全关 = T06 单向量基线时序。
- 关键词 / 向量融合权重、候选池大小、多查询数量都可经环境变量覆盖。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "environment": "test",
        "siliconflow_api_key": None,
    }
    return Settings(**base, **overrides)  # type: ignore[arg-type]


def test_default_combo_matches_t07_ablation_choice() -> None:
    """默认组合 = T07 消融 F/H 行:重排 + 查询改写(Getting Recall@3/MRR 饱和且开销最小)。"""
    settings = _settings()

    assert settings.use_query_rewrite is True
    assert settings.use_rerank is True
    assert settings.use_multi_query is False
    assert settings.use_hybrid is False
    assert settings.use_mmr is False


def test_toggles_parse_from_env_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_HYBRID", "1")
    monkeypatch.setenv("USE_RERANK", "true")
    monkeypatch.setenv("USE_MULTI_QUERY", "yes")
    monkeypatch.setenv("USE_QUERY_REWRITE", "false")
    monkeypatch.setenv("USE_MMR", "0")

    settings = _settings()

    assert settings.use_hybrid is True
    assert settings.use_rerank is True
    assert settings.use_multi_query is True
    assert settings.use_query_rewrite is False
    assert settings.use_mmr is False


def test_fusion_params_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_VECTOR_WEIGHT", "0.6")
    monkeypatch.setenv("RETRIEVAL_KEYWORD_WEIGHT", "0.4")
    monkeypatch.setenv("RETRIEVAL_CANDIDATE_K", "50")
    monkeypatch.setenv("RETRIEVAL_MULTI_QUERY_COUNT", "2")
    monkeypatch.setenv("RETRIEVAL_MMR_LAMBDA", "0.5")

    settings = _settings()

    assert settings.retrieval_vector_weight == pytest.approx(0.6)
    assert settings.retrieval_keyword_weight == pytest.approx(0.4)
    assert settings.retrieval_candidate_k == 50
    assert settings.retrieval_multi_query_count == 2
    assert settings.retrieval_mmr_lambda == pytest.approx(0.5)


def test_both_fusion_weights_zero_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(retrieval_vector_weight=0.0, retrieval_keyword_weight=0.0)


def test_negative_candidate_k_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(retrieval_candidate_k=0)


def test_fusion_params_have_sane_defaults() -> None:
    settings = _settings()

    assert settings.retrieval_vector_weight == pytest.approx(0.7)
    assert settings.retrieval_keyword_weight == pytest.approx(0.3)
    assert settings.retrieval_candidate_k == 30
    assert settings.retrieval_multi_query_count == 3
    assert settings.retrieval_mmr_lambda == pytest.approx(0.7)
