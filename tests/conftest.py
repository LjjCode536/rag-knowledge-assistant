from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.main import create_app
from tests.pg_utils import PgTestDatabase, fresh_test_database, pg_available


@pytest.fixture
def upload_dir(tmp_path: Path) -> Path:
    """上传落盘目录指向 pytest 临时目录,验证落盘位置可配置且不污染仓库。"""
    return tmp_path / "uploaded_docs"


@pytest.fixture
def test_pg_database() -> Iterator[PgTestDatabase]:
    """每用例一张重建过的 rag_test 库;后端不可达(无 Docker)时整批跳过。

    建表走生产同一 DDL(ensure_schema),仓储与向量库的真实落库都在这里验证。
    """
    if not pg_available():
        pytest.skip("PostgreSQL 测试后端不可达(docker compose up -d db 启动)")
    db = fresh_test_database()
    yield db
    # 外部清理即可:pool 由各实现自己 close,库留给下一用例整库重建


@pytest.fixture
def settings(upload_dir: Path) -> Settings:
    """测试配置:不读 .env、无密钥、JSON 日志、上传落盘进临时目录。

    入库固定同步形态(M7 之前的主缝口径):既有 T02-T13 的契约测试与评估
    以此保持不变;T14 异步管线的验收在 test_ingestion_async_api.py 单独
    打开 ingestion_async=True 验证,生产默认值为 True(见 ADR-0012)。

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
        ingestion_async=False,
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
