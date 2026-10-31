"""窄接缝:配置覆盖后仍受跨字段不变量约束。

``model_copy`` 不重跑 ``model_validator``:脚本层用它覆盖参数会带着
非法配置跑完整轮评估。``with_overrides`` 重跑校验器,跨字段不变量
(如 pgvector 模式必须给 database_url)在覆盖后依然生效。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_with_overrides_keeps_valid_config_valid():
    base = Settings(_env_file=None, vector_store="memory")

    updated = base.with_overrides(chunk_size=800, chunk_overlap=100)

    assert updated.chunk_size == 800
    assert updated.chunk_overlap == 100
    # 未被覆盖的字段保持原值
    assert updated.chunk_strategy == base.chunk_strategy


def test_with_overrides_rejects_invalid_cross_field_combination():
    """覆盖成非法组合时直接拒绝,而不是带着非法配置继续跑。"""
    base = Settings(_env_file=None, vector_store="memory")

    with pytest.raises(ValidationError):
        base.with_overrides(retrieval_vector_weight=0.0, retrieval_keyword_weight=0.0)


def test_with_overrides_rejects_pgvector_without_database_url():
    """pgvector 模式缺连接串:覆盖后仍必须报错。"""
    base = Settings(_env_file=None, vector_store="memory", database_url=None)

    with pytest.raises(ValidationError):
        base.with_overrides(vector_store="pgvector")


def test_with_overrides_does_not_mutate_the_original():
    base = Settings(_env_file=None, chunk_size=500)

    base.with_overrides(chunk_size=900)

    assert base.chunk_size == 500
