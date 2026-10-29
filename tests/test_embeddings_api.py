"""HTTP 主接缝:批量向量化与内存向量库入库(T04 / M3 基线上半)。

替身上游:注入确定性假向量替身,往返与过滤全程不触网;
未配置密钥路径直接走统一客户端的真实错误映射。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.errors import UpstreamTimeoutError
from app.main import create_app
from app.services.chunking import ChunkingService
from app.services.embedding import EmbeddingService
from tests.fake_embedder import DeterministicEmbedder
from tests.support import json_log_entries

DOCUMENTS_URL = "/api/v1/documents"
SEARCH_URL = "/api/v1/search"

ALPHA_TEXT = "休假 工资\n\n休假 补贴 咨询\n\n工资 计算手册\n"
BETA_TEXT = "运维 值班 监控告警生产环境\n"


def _upload(client: TestClient, name: str, content: str, **form: str) -> dict:
    response = client.post(
        DOCUMENTS_URL, files={"file": (name, content.encode(), "text/plain")}, data=form
    )
    assert response.status_code == 201, response.text
    return response.json()


def _chunk(client: TestClient, document_id: str) -> list[dict]:
    response = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks", json={"strategy": "element"}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["chunks"]


def _embed(client: TestClient, document_id: str) -> dict:
    return client.post(f"{DOCUMENTS_URL}/{document_id}/embeddings")


def _index(client: TestClient, name: str, content: str, **form: str) -> dict:
    document_id = _upload(client, name, content, **form)["data"]["id"]
    chunks = _chunk(client, document_id)
    response = _embed(client, document_id)
    assert response.status_code == 200, response.text
    return {"document_id": document_id, "chunks": chunks, "run": response.json()}


@pytest.fixture
def fake_client(settings, upload_dir) -> Iterator[TestClient]:
    """注入确定性假向量替身的应用,向量全程本地产生。"""
    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


class TimeoutEmbedder:
    """直接抛统一客户端超时错误的替身,验证失败路径不动文档状态。"""

    async def embed(self, texts, *, model=None):
        raise UpstreamTimeoutError(operation="embed", details={"attempt": 1})


def test_unconfigured_key_returns_unified_upstream_error_and_keeps_document_indexable(
    client,
):
    body = _upload(client, "hr.txt", ALPHA_TEXT)
    document_id = body["data"]["id"]
    _chunk(client, document_id)

    response = _embed(client, document_id)

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "UPSTREAM_NOT_CONFIGURED"
    assert payload["details"]["provider"] == "siliconflow"
    assert payload["trace_id"] == response.headers["X-Request-ID"]
    # 上游失败不动文档状态:仍停在 chunked,补配密钥后可直接重试
    view = client.get(f"{DOCUMENTS_URL}/{document_id}").json()["data"]
    assert view["status"] == "chunked"
    assert view["indexed_at"] is None


def test_embed_roundtrip_marks_document_indexed(fake_client):
    document_id = _upload(
        fake_client,
        "hr.txt",
        ALPHA_TEXT,
        tenant="acme",
        department="人力部",
        doc_type="政策",
        version="v2",
    )["data"]["id"]
    chunks = _chunk(fake_client, document_id)

    response = _embed(fake_client, document_id)

    assert response.status_code == 200
    assert response.json()["trace_id"] == response.headers["X-Request-ID"]
    data = response.json()["data"]
    assert data["document"]["status"] == "indexed"
    assert data["document"]["indexed_at"] is not None
    assert data["chunk_count"] == len(chunks) == 3
    assert data["model"] == "BAAI/bge-m3"
    assert data["prompt_tokens"] > 0
    assert data["total_tokens"] >= data["prompt_tokens"]
    assert data["latency_ms"] >= 0

    view = fake_client.get(f"{DOCUMENTS_URL}/{document_id}").json()["data"]
    assert view["status"] == "indexed"
    assert view["indexed_at"] is not None


def test_embed_logs_usage_and_latency_structured(fake_client, capsys):
    _index(fake_client, "hr.txt", ALPHA_TEXT, tenant="acme")

    entries = json_log_entries(capsys.readouterr().out)
    embedded = [entry for entry in entries if entry["event"] == "document_embedded"]
    assert len(embedded) == 1
    log = embedded[0]
    assert log["model"] == "BAAI/bge-m3"
    assert log["chunks"] == 3
    assert log["prompt_tokens"] > 0
    assert log["completion_tokens"] == 0
    assert log["total_tokens"] >= log["prompt_tokens"]  # 用量
    assert log["latency_ms"] >= 0
    assert log["duration_ms"] >= 0


def test_embed_upstream_failure_leaves_document_indexable_again(
    upload_dir, settings, capsys
):
    app = create_app(settings, embedder=TimeoutEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        document_id = _upload(client, "hr.txt", ALPHA_TEXT)["data"]["id"]
        _chunk(client, document_id)

        response = _embed(client, document_id)

        assert response.status_code == 504
        assert response.json()["code"] == "UPSTREAM_TIMEOUT"
        view = client.get(f"{DOCUMENTS_URL}/{document_id}").json()["data"]
        assert view["status"] == "chunked"  # 可重试,不会被一次失败打死

        # 失败侧同样留用量与延迟基线(ADR-0003)
        entries = json_log_entries(capsys.readouterr().out)
        failed = [entry for entry in entries if entry["event"] == "document_embedding_failed"]
        assert len(failed) == 1
        assert failed[0]["error_code"] == "UPSTREAM_TIMEOUT"
        assert failed[0]["chunks"] == 3
        assert failed[0]["duration_ms"] >= 0


def test_rechunk_during_embedding_rejects_stale_vectors(settings, upload_dir):
    """并发竞态:向量化进行中切片被整组替换,入库拒绝且不写陈旧向量。

    单进程测试无法排程真实并发请求,竞态由服务内部引用白盒注入;
    断言全部走 HTTP 公共接缝与检索接口。
    """
    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        document_id = _upload(client, "hr.txt", ALPHA_TEXT)["data"]["id"]
        _chunk(client, document_id)

        embedding_service: EmbeddingService = app.state.embedding_service
        chunking_service: ChunkingService = app.state.chunking_service

        class RechunkDuringEmbed:
            async def embed(self, texts, *, model=None):
                chunking_service.chunk(document_id)  # 模拟并发重切
                return await DeterministicEmbedder().embed(texts, model=model)

        app.state.embedding_service = EmbeddingService(
            document_repository=embedding_service._documents,
            chunk_repository=embedding_service._chunks,
            vector_store=app.state.vector_store,
            embedder=RechunkDuringEmbed(),
        )

        response = _embed(client, document_id)

        assert response.status_code == 409
        assert response.json()["code"] == "INVALID_DOCUMENT_STATE"
        view = client.get(f"{DOCUMENTS_URL}/{document_id}").json()["data"]
        assert view["status"] == "chunked"
        assert view["indexed_at"] is None
        # 陈旧切片的向量不得入库:检索不到任何结果
        data = client.post(SEARCH_URL, json={"query": "休假 工资"}).json()["data"]
        assert data["total"] == 0


def test_embed_unknown_document_returns_404(client):
    response = _embed(client, "no-such-document")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["details"] == {"document_id": "no-such-document"}


def test_embed_unchunked_document_returns_409(client):
    document_id = _upload(client, "hr.txt", ALPHA_TEXT)["data"]["id"]

    response = _embed(client, document_id)

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "INVALID_DOCUMENT_STATE"
    assert body["details"]["status"] == "parsed"


def test_embed_document_without_chunks_returns_409(client):
    document_id = _upload(client, "empty.txt", "")["data"]["id"]
    _chunk(client, document_id)
    view = client.get(f"{DOCUMENTS_URL}/{document_id}").json()["data"]
    assert view["status"] == "chunked"

    response = _embed(client, document_id)

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_DOCUMENT_STATE"


def test_reembed_after_indexed_returns_409(fake_client):
    document_id = _upload(fake_client, "hr.txt", ALPHA_TEXT)["data"]["id"]
    _chunk(fake_client, document_id)
    assert _embed(fake_client, document_id).status_code == 200

    response = _embed(fake_client, document_id)

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_DOCUMENT_STATE"
