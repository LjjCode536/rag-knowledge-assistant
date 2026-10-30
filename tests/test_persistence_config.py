"""配置接缝:持久化模式选择(M3 第三层 pgvector)。

memory / chroma 行为不变;pgvector 选型必须同时给出数据库连接串,
在配置期直接拒绝而不是启动后静默回退。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_pgvector_is_a_valid_vector_store_choice():
    """pgvector 进入 vector_store 选项:选型 PG 生产持久化(M3 第三层)。"""
    settings = Settings(
        _env_file=None,
        vector_store="pgvector",
        database_url="postgresql://rag:rag@localhost:5432/rag",
    )
    assert settings.vector_store == "pgvector"
    assert settings.database_url == "postgresql://rag:rag@localhost:5432/rag"


def test_pgvector_requires_database_url():
    """选 pgvector 却没给连接串:配置期报错,绝不静默回退内存态。"""
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None, vector_store="pgvector")


def test_database_url_not_required_for_memory_and_chroma():
    """memory / chroma 回退路径零数据库依赖:不配 database_url 照常启动。"""
    for store in ("memory", "chroma"):
        settings = Settings(_env_file=None, vector_store=store)
        assert settings.database_url is None
