"""HTTP 主接缝:文档版本退位与删除(T10 / 教材第 45 章 reindex 流程)。

覆盖:上新即退位、检索只面向最新版本、删除连带清理、失败带原因且可重试;
替身上游:向量全程本地产生,不触任何模型服务。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from app.repositories.vector_memory import InMemoryVectorStore
from tests.support import json_log_entries
from tests.test_embeddings_api import _chunk, _embed, _index

DOCUMENTS_URL = "/api/v1/documents"
SEARCH_URL = "/api/v1/search"

OLD_TEXT = "休假 工资\n\n休假 补贴 咨询\n\n工资 计算手册\n"
NEW_TEXT = "报销 发票 流程\n\n发票 抬头 开具\n\n报销 时效 指南\n"

QUERY = "休假 工资 运维"
OLD_VOCAB_QUERY = "休假 工资"
NEW_VOCAB_QUERY = "报销 发票"


def _search(client: TestClient, query: str, top_k: int = 10) -> dict:
    response = client.post(SEARCH_URL, json={"query": query, "top_k": top_k})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _fetch(client: TestClient, document_id: str) -> dict:
    response = client.get(f"{DOCUMENTS_URL}/{document_id}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _upload_raw(client: TestClient, content: str) -> dict:
    """上传(hr.txt 正文返回 201),返回档案数据;退位在上新即发生。"""
    response = client.post(
        DOCUMENTS_URL, files={"file": ("hr.txt", content.encode(), "text/plain")}
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


@pytest.fixture
def fake_app(settings):
    """注入确定性假向量替身与内存向量库的完整应用。"""
    return create_app(settings, embedder=DeterministicEmbedder())


# ===== 版本退位:上新即退位,检索只面向最新版本,旧版本向量无残留 =====


def test_new_version_supersedes_old_version(fake_app):
    with TestClient(fake_app, raise_server_exceptions=False) as client:
        old = _index(client, "hr.txt", OLD_TEXT)["document_id"]
        before = _fetch(client, old)
        assert before["status"] == "indexed"
        assert before["is_latest"] is True
        assert _search(client, OLD_VOCAB_QUERY)["total"] == 3

        # 新版本尚未入库完成:旧版本在上新时即退位,而非等新版本 indexed
        new = _upload_raw(client, NEW_TEXT)["id"]
        after_upload = _fetch(client, old)
        assert after_upload["status"] == "superseded"
        assert after_upload["is_latest"] is False
        assert after_upload["superseded_at"] is not None

        _chunk(client, new)
        _embed(client, new)
        latest = _fetch(client, new)
        assert latest["status"] == "indexed"
        assert latest["is_latest"] is True

        # 检索只返回新版本,旧版本切片无残留(即便查询词面完全来自旧版本);
        # 基线检索不设分数门槛,所以这里断言的是来源而非命中数
        result = _search(client, OLD_VOCAB_QUERY)
        assert all(hit["chunk"]["document_id"] != old for hit in result["hits"])
        result = _search(client, QUERY)
        assert all(hit["chunk"]["document_id"] != old for hit in result["hits"])
        new_only = _search(client, NEW_VOCAB_QUERY)
        assert {hit["chunk"]["document_id"] for hit in new_only["hits"]} == {new}


def test_superseded_version_keeps_chunk_history_but_not_retrieval(fake_app):
    with TestClient(fake_app, raise_server_exceptions=False) as client:
        old = _index(client, "hr.txt", OLD_TEXT)["document_id"]
        old_chunks = client.get(f"{DOCUMENTS_URL}/{old}/chunks").json()["data"]["chunks"]
        assert old_chunks

        _upload_raw(client, NEW_TEXT)

        # 退位版本切片保留为历史可查,但检索通道不再可见
        chunks = client.get(f"{DOCUMENTS_URL}/{old}/chunks").json()["data"]
        assert chunks["chunk_count"] == len(old_chunks)
        assert [item["chunk_id"] for item in chunks["chunks"]] == [
            item["chunk_id"] for item in old_chunks
        ]
        result = _search(client, OLD_VOCAB_QUERY)
        assert all(hit["chunk"]["document_id"] != old for hit in result["hits"])


def test_superseded_document_cannot_reenter_pipeline(fake_app):
    """终态防线:退位版本不得通过切片 / 向量化端点重新进入检索池。"""
    with TestClient(fake_app, raise_server_exceptions=False) as client:
        old = _index(client, "hr.txt", OLD_TEXT)["document_id"]
        _upload_raw(client, NEW_TEXT)
        assert _fetch(client, old)["status"] == "superseded"

        assert client.post(f"{DOCUMENTS_URL}/{old}/chunks", json={}).status_code == 409
        assert client.post(f"{DOCUMENTS_URL}/{old}/embeddings").status_code == 409
        assert _fetch(client, old)["status"] == "superseded"


def test_hybrid_keyword_channel_also_faces_latest_version(settings):
    settings = settings.model_copy(update={"use_hybrid": True})
    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        old = _index(client, "hr.txt", OLD_TEXT)["document_id"]
        new = _upload_raw(client, NEW_TEXT)["id"]
        _chunk(client, new)
        _embed(client, new)

        for query in (OLD_VOCAB_QUERY, QUERY, NEW_VOCAB_QUERY):
            result = _search(client, query)
            assert all(
                hit["chunk"]["document_id"] != old for hit in result["hits"]
            ), query


def test_same_file_name_in_different_tenants_do_not_supersede(fake_app):
    with TestClient(fake_app, raise_server_exceptions=False) as client:
        acme = _index(client, "hr.txt", OLD_TEXT, tenant="acme")["document_id"]
        beta = _index(client, "hr.txt", NEW_TEXT, tenant="beta")["document_id"]

        assert _fetch(client, acme)["is_latest"] is True
        assert _fetch(client, beta)["is_latest"] is True
        # 两份同名不同租户的文档互不退位、各自可检索
        old_hits = _search(client, OLD_VOCAB_QUERY)
        assert any(hit["chunk"]["document_id"] == acme for hit in old_hits["hits"])
        new_hits = _search(client, NEW_VOCAB_QUERY)
        assert any(hit["chunk"]["document_id"] == beta for hit in new_hits["hits"])


# ===== 删除:切片与向量连带清理,检索无该文档任何切片 =====


def test_delete_cascades_chunks_and_vectors(fake_app):
    with TestClient(fake_app, raise_server_exceptions=False) as client:
        alive = _index(client, "hr.txt", OLD_TEXT)["document_id"]
        doomed_indexed = _index(client, "ops.txt", "运维 值班 监控告警\n")
        doomed = doomed_indexed["document_id"]
        doomed_chunks = doomed_indexed["chunks"]
        assert doomed_chunks

        response = client.delete(f"{DOCUMENTS_URL}/{doomed}")

        assert response.status_code == 200
        assert response.json()["code"] == "OK"
        assert response.json()["trace_id"] == response.headers["X-Request-ID"]
        view = response.json()["data"]
        assert view["status"] == "deleted"
        assert view["is_latest"] is False
        assert view["deleted_at"] is not None

        fetched = _fetch(client, doomed)
        assert fetched["status"] == "deleted"
        assert fetched["deleted_at"] == view["deleted_at"]
        chunks = client.get(f"{DOCUMENTS_URL}/{doomed}/chunks").json()["data"]
        assert chunks["chunk_count"] == 0
        assert chunks["chunks"] == []

        # 检索无该文档任何切片,其余文档不受连带影响
        retired = _search(client, OLD_VOCAB_QUERY)
        assert alive in {hit["chunk"]["document_id"] for hit in retired["hits"]}
        mixed = _search(client, QUERY)
        assert all(hit["chunk"]["document_id"] != doomed for hit in mixed["hits"])
        assert _search(client, NEW_VOCAB_QUERY)["total"] > 0


def test_delete_is_idempotent(fake_app):
    with TestClient(fake_app, raise_server_exceptions=False) as client:
        doomed = _index(client, "hr.txt", OLD_TEXT)["document_id"]
        first = client.delete(f"{DOCUMENTS_URL}/{doomed}")
        assert first.status_code == 200
        second = client.delete(f"{DOCUMENTS_URL}/{doomed}")

        assert second.status_code == 200
        assert second.json()["code"] == "OK"
        assert second.json()["data"]["status"] == "deleted"


def test_delete_unknown_document_returns_404(fake_app):
    with TestClient(fake_app, raise_server_exceptions=False) as client:
        response = client.delete(f"{DOCUMENTS_URL}/no-such-document")

        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"
        assert response.json()["details"] == {"document_id": "no-such-document"}


def test_delete_failed_document_lands_in_deleted_state(fake_app):
    with TestClient(fake_app, raise_server_exceptions=False) as client:
        doomed = client.post(
            DOCUMENTS_URL,
            files={"file": ("broken.pdf", b"this is not a pdf", "application/pdf")},
        ).json()["data"]["id"]
        assert _fetch(client, doomed)["status"] == "failed"

        response = client.delete(f"{DOCUMENTS_URL}/{doomed}")

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "deleted"


# ===== 失败带原因且可重试:清理失败原状保留,重试即恢复 =====


class FailedOnceVectorStore(InMemoryVectorStore):
    """第一次 delete 调用失败的替身向量库;replace 语义完全正常。"""

    def __init__(self) -> None:
        super().__init__()
        self._failures_left = 1

    def delete(self, document_id: str) -> None:
        if self._failures_left > 0:
            self._failures_left -= 1
            raise RuntimeError("存储句柄不可用")
        super().delete(document_id)


def test_delete_failure_keeps_record_and_reason_then_retry_succeeds(settings):
    app = create_app(
        settings, embedder=DeterministicEmbedder(), vector_store=FailedOnceVectorStore()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        doomed = _index(client, "hr.txt", OLD_TEXT)["document_id"]
        before = _fetch(client, doomed)

        response = client.delete(f"{DOCUMENTS_URL}/{doomed}")

        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "DOCUMENT_DELETE_FAILED"
        assert body["details"]["document_id"] == doomed
        assert body["details"]["retryable"] is True
        assert body["details"]["reason"]

        # 失败不落终态:档案原状保留、原因可查、重试即恢复
        fetched = _fetch(client, doomed)
        assert fetched["status"] == before["status"] == "indexed"
        assert fetched["error_message"] == body["details"]["reason"]
        assert fetched["is_latest"] is True
        assert _search(client, OLD_VOCAB_QUERY)["total"] == 3

        response = client.delete(f"{DOCUMENTS_URL}/{doomed}")

        assert response.status_code == 200
        fetched = _fetch(client, doomed)
        assert fetched["status"] == "deleted"
        assert fetched["error_message"] is None
        assert _search(client, OLD_VOCAB_QUERY)["total"] == 0


def test_supersede_failure_keeps_latest_and_reason_then_retry_succeeds(settings):
    app = create_app(
        settings, embedder=DeterministicEmbedder(), vector_store=FailedOnceVectorStore()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        old = _index(client, "hr.txt", OLD_TEXT)["document_id"]

        response = client.post(
            DOCUMENTS_URL, files={"file": ("hr.txt", NEW_TEXT.encode(), "text/plain")}
        )

        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "DOCUMENT_SUPERSEDE_FAILED"
        assert body["details"]["document_id"] == old
        assert body["details"]["retryable"] is True
        assert body["details"]["reason"]

        # 上新失败不留残档:旧版本仍是最新且可检索,重传即从退位步骤重试
        fetched = _fetch(client, old)
        assert fetched["status"] == "indexed"
        assert fetched["is_latest"] is True
        assert fetched["error_message"] == body["details"]["reason"]
        assert _search(client, OLD_VOCAB_QUERY)["total"] == 3

        response = client.post(
            DOCUMENTS_URL, files={"file": ("hr.txt", NEW_TEXT.encode(), "text/plain")}
        )
        assert response.status_code == 201
        latest = _fetch(client, old)
        assert latest["status"] == "superseded"
        assert latest["is_latest"] is False


# ===== 状态机与结构化日志:退位与删除全程可查 =====


def test_supersede_and_delete_log_structured_events(fake_app, capsys):
    with TestClient(fake_app, raise_server_exceptions=False) as client:
        doomed = _index(client, "hr.txt", OLD_TEXT)["document_id"]
        # 新版本上新:旧版本退位;随后删除退位版本:终态与日志全程可查
        _upload_raw(client, NEW_TEXT)
        client.delete(f"{DOCUMENTS_URL}/{doomed}")

        entries = json_log_entries(capsys.readouterr().out)
        deleted = [entry for entry in entries if entry["event"] == "document_deleted"]
        assert len(deleted) == 1
        assert deleted[0]["document_id"] == doomed
        assert deleted[0]["file_name"] == "hr.txt"
        assert deleted[0]["duration_ms"] >= 0
        superseded = [entry for entry in entries if entry["event"] == "document_superseded"]
        assert len(superseded) == 1
        assert superseded[0]["document_id"] == doomed
