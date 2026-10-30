"""HTTP 主接缝:异步入库管线(T14 / M7·上)。

验收口径:大文档上传立即返回(响应快照停在 uploaded);进度经状态机可查,
失败带原因且可重试;异步与同步路径(ingestion_async 开关)行为一致。
替身上游:向量经确定性假替身本地产生,全程不触网。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.errors import UpstreamTimeoutError
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from tests.fake_pdf import build_pdf
from tests.support import json_log_entries

DOCUMENTS_URL = "/api/v1/documents"
SEARCH_URL = "/api/v1/search"

ALPHA_TEXT = "休假 工资\n\n休假 补贴 咨询\n\n工资 计算手册\n"


def _upload(
    client: TestClient, *, name: str, content: bytes, mime: str = "text/plain", **form: str
) -> dict:
    response = client.post(
        DOCUMENTS_URL, files={"file": (name, content, mime)}, data=form
    )
    assert response.status_code == 201, response.text
    return response.json()


def _view(client: TestClient, document_id: str) -> dict:
    response = client.get(f"{DOCUMENTS_URL}/{document_id}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.fixture
def async_client(settings):
    """异步形态应用:注入确定性假向量替身,管线全程本地完成。"""
    app = create_app(
        settings.model_copy(update={"ingestion_async": True}),
        embedder=DeterministicEmbedder(),
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# ===== 上传立即返回:响应快照停在登记后的 uploaded,重活由管线在后台完成 =====


def test_upload_returns_immediately_and_pipeline_brings_document_indexable(async_client):
    body = _upload(async_client, name="hr.txt", content=ALPHA_TEXT.encode())

    assert body["code"] == "OK"
    data = body["data"]
    # 请求不阻塞在解析:序列化发生在后台任务启动前,快照停在登记后的状态
    assert data["status"] == "uploaded"
    assert data["error_message"] is None
    document_id = data["id"]

    # 管线推进结果经状态机可查:完成即入库(indexed),随后即可问答
    view = _view(async_client, document_id)
    assert view["status"] == "indexed"
    assert view["indexed_at"] is not None
    assert view["chunk_count"] > 0

    hits = async_client.post(SEARCH_URL, json={"query": "休假 工资"}).json()["data"]
    assert hits["total"] > 0
    assert all(hit["chunk"]["document_id"] == document_id for hit in hits["hits"])


# ===== 失败带原因且可重试:解析失败落 failed,重传同标识即重试 =====


def test_parse_failure_lands_failed_with_reason_and_reupload_retries(async_client):
    body = _upload(
        async_client, name="report.pdf", content=b"this is not a pdf", mime="application/pdf"
    )
    document_id = body["data"]["id"]

    view = _view(async_client, document_id)
    assert view["status"] == "failed"
    assert "PDF" in view["error_message"]
    assert view["failed_at"] is not None

    # 重试路径(ADR-0009):重传同标识新版本,failed 版本退位、新版本照常入库
    body = _upload(
        async_client,
        name="report.pdf",
        content=build_pdf(["Refunds take seven days."]),
        mime="application/pdf",
    )
    new_view = _view(async_client, body["data"]["id"])
    assert new_view["status"] == "indexed"

    old_view = _view(async_client, document_id)
    assert old_view["status"] == "superseded"
    assert old_view["is_latest"] is False


# ===== 上游向量化失败:不落 failed,停在 chunked 重试即是补挂号 =====


class FlakyEmbedder:
    """第一次调用抛上游超时,其后走确定性成功:验证"停在 chunked 可重试"。"""

    def __init__(self) -> None:
        self.calls = 0
        self._inner = DeterministicEmbedder()

    async def embed(self, texts, *, model=None):
        self.calls += 1
        if self.calls == 1:
            raise UpstreamTimeoutError(operation="embed", details={"attempt": 1})
        return await self._inner.embed(texts, model=model)


def test_upstream_embed_failure_keeps_document_chunked_and_manual_retry_indexes(
    settings,
):
    app = create_app(
        settings.model_copy(update={"ingestion_async": True}), embedder=FlakyEmbedder()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        body = _upload(client, name="hr.txt", content=ALPHA_TEXT.encode())
        document_id = body["data"]["id"]

        # 管线中的向量化失败不动状态:档案停在 chunked,补齐后可重试(ADR-0003)
        view = _view(client, document_id)
        assert view["status"] == "chunked"
        assert view["indexed_at"] is None

        response = client.post(f"{DOCUMENTS_URL}/{document_id}/embeddings")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["document"]["status"] == "indexed"

        view = _view(client, document_id)
        assert view["status"] == "indexed"


# ===== 空文档:管线推进到 chunked,留档可查、手工重试仍按 409 语义 =====


def test_empty_document_parks_at_chunked_with_upstream_note(async_client, capsys):
    body = _upload(async_client, name="empty.txt", content=b"")
    document_id = body["data"]["id"]

    view = _view(async_client, document_id)
    assert view["status"] == "chunked"
    assert view["indexed_at"] is None
    assert view["chunk_count"] == 0

    entries = json_log_entries(capsys.readouterr().out)
    note = [
        entry
        for entry in entries
        if entry["event"] == "document_ingest_stage_refused"
    ]
    assert len(note) == 1
    assert note[0]["error_code"] == "INVALID_DOCUMENT_STATE"


# ===== 异步与同步路径行为一致:同一服务同一落点,检索面逐位一致 =====


def test_async_and_sync_paths_land_same_state_and_same_search_results(settings):
    sync_app = create_app(
        settings.model_copy(update={"ingestion_async": False}),
        embedder=DeterministicEmbedder(),
    )
    async_app = create_app(
        settings.model_copy(update={"ingestion_async": True}),
        embedder=DeterministicEmbedder(),
    )

    def run_pipeline(client: TestClient, *, async_mode: bool) -> tuple[dict, list]:
        with TestClient(client, raise_server_exceptions=False) as in_pipeline_client:
            body = _upload(
                in_pipeline_client,
                name="hr.txt",
                content=ALPHA_TEXT.encode(),
                tenant="acme",
                department="人力部",
                doc_type="政策",
                version="v2",
            )
            document_id = body["data"]["id"]
            if not async_mode:
                # 同步形态:上传响应即解析结果,切片与向量化走手动端点
                assert body["data"]["status"] == "parsed"
                assert in_pipeline_client.post(
                    f"{DOCUMENTS_URL}/{document_id}/chunks"
                ).status_code == 200
                assert in_pipeline_client.post(
                    f"{DOCUMENTS_URL}/{document_id}/embeddings"
                ).status_code == 200
            view = _view(in_pipeline_client, document_id)
            data = in_pipeline_client.post(
                SEARCH_URL, json={"query": "休假 工资 运维", "top_k": 5}
            ).json()["data"]
            hits = [
                (hit["chunk"]["index"], hit["chunk"]["content"], hit["score"])
                for hit in data["hits"]
            ]
            return view, hits

    sync_view, sync_hits = run_pipeline(sync_app, async_mode=False)
    async_view, async_hits = run_pipeline(async_app, async_mode=True)

    assert (sync_view["status"], sync_view["chunk_count"]) == (
        async_view["status"],
        async_view["chunk_count"],
    ) == ("indexed", 1)  # 同一切分配置:三段短散文合并成一片,两条路径一致
    assert sync_hits == async_hits[: len(sync_hits)]
    assert sync_view["error_message"] == async_view["error_message"] is None


# ===== 生命周期迁移打进管线:档案落终态,管线让位不产出陈旧入库 =====


def test_delete_during_pipeline_wins_and_pipeline_yields(settings):
    class DeleteDuringEmbed:
        """向量化替身:先并发删除该文档,再继续向量化,模拟生命周期竞态。"""

        def __init__(self) -> None:
            self._inner = DeterministicEmbedder()
            self._documents = None
            self._lifecycle = None

        def wire(self, document_repository, lifecycle_service) -> None:
            self._documents = document_repository
            self._lifecycle = lifecycle_service

        async def embed(self, texts, *, model=None):
            record = next(iter(self._documents._records.values()))
            self._lifecycle.delete(record.id)
            return await self._inner.embed(texts, model=model)

    embedder = DeleteDuringEmbed()
    app = create_app(
        settings.model_copy(update={"ingestion_async": True}), embedder=embedder
    )
    embedder.wire(
        embedder_ref := app.state.embedding_service._documents,
        app.state.lifecycle_service,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        body = _upload(client, name="hr.txt", content=ALPHA_TEXT.encode())
        document_id = body["data"]["id"]
        assert document_id == next(iter(embedder_ref._records))

        # 删除抢先迁移到 deleted 终态:管线不推进、不产出向量,让位终态
        view = _view(client, document_id)
        assert view["status"] == "deleted"
        assert view["deleted_at"] is not None

        data = client.post(SEARCH_URL, json={"query": "休假 工资"}).json()["data"]
        assert data["total"] == 0


# ===== 结构化日志:管线从起步到收口全程留痕 =====


def test_pipeline_logs_started_and_finished_structured(async_client, capsys):
    _upload(async_client, name="hr.txt", content=ALPHA_TEXT.encode())

    entries = json_log_entries(capsys.readouterr().out)
    by_event = {entry["event"]: entry for entry in entries}
    assert "document_ingest_finished" in by_event
    finished = by_event["document_ingest_finished"]
    assert finished["status"] == "indexed"
    assert finished["document_id"]
    assert finished["duration_ms"] >= 0
    # 各阶段服务的事件同批可见:解析 / 切片 / 向量化全程可追溯
    for event in ("document_parsed", "document_chunked", "document_embedded"):
        assert event in by_event
