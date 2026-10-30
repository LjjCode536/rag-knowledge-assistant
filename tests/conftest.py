from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.main import create_app


@pytest.fixture
def upload_dir(tmp_path: Path) -> Path:
    """上传落盘目录指向 pytest 临时目录,验证落盘位置可配置且不污染仓库。"""
    return tmp_path / "uploaded_docs"


@pytest.fixture
def settings(upload_dir: Path) -> Settings:
    """测试配置:不读 .env、无密钥、JSON 日志、上传落盘进临时目录。

    生成器固定规则版:离线基线口径;真实 LLM 生成在 test_generation_llm.py
    与 test_chat_llm_api.py 用 keyed 配置 + 假传输层单独验证。
    """
    return Settings(
        _env_file=None,
        environment="test",
        siliconflow_api_key=None,
        log_format="json",
        log_level="INFO",
        upload_dir=upload_dir,
        qa_generator="rule",
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def probe_app(app):
    """在真实应用上挂载探针路由,用于验证各错误路径。"""

    @app.get("/__probe/validated")
    async def validated(q: int) -> dict[str, int]:
        return {"q": q}

    @app.get("/__probe/business")
    async def business_error() -> None:
        raise NotFoundError("知识库文档不存在")

    @app.get("/__probe/boom")
    async def boom() -> None:
        raise RuntimeError("secret-stack-marker")

    return app


@pytest.fixture
def probe_client(probe_app) -> Iterator[TestClient]:
    with TestClient(probe_app, raise_server_exceptions=False) as test_client:
        yield test_client
