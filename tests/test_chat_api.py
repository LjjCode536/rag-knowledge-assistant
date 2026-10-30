"""HTTP 主接缝:最小在线问答链路(T05 / M3 基线下半)。

替身上游:注入确定性假向量替身与可注入生成器,问答全程不触网;
覆盖有据回答 + 完整引用、低分拒答、查询日志与错误路径。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.domain.qa import AnswerContext
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from tests.fake_pdf import build_pdf

DOCUMENTS_URL = "/api/v1/documents"
CHAT_URL = "/api/v1/chat"
QUERY_LOGS_URL = "/api/v1/query-logs"

HANDBOOK = "# 休假制度\n\n员工入职满一年后,可以申请年假。\n"

LEAVE_QUESTION = "入职多久可以申请年假?"
UNRELATED_QUESTION = "垃圾分类怎么处理?"


class RecordingGenerator:
    """可注入生成器替身:记录收到的编号上下文,返回固定有据回答。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, AnswerContext]] = []

    async def generate(self, question: str, *, context: AnswerContext) -> str:
        self.calls.append((question, context))
        return "根据知识库资料,这是替身回答。"


class RefusingGenerator:
    """总是拒答的生成器替身:验证拒答时不把命中误标为回答证据。"""

    async def generate(self, question: str, *, context: AnswerContext) -> str:
        return "根据当前资料无法确认。"


def _index(client: TestClient, name: str, content: str | bytes, **form: str) -> dict:
    """上传 → 按标题切片 → 向量化入库,返回文档 id 与切片。"""
    payload = content.encode() if isinstance(content, str) else content
    response = client.post(
        DOCUMENTS_URL, files={"file": (name, payload, "text/plain")}, data=form
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["data"]["id"]

    response = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks", json={"strategy": "heading"}
    )
    assert response.status_code == 200, response.text
    chunks = response.json()["data"]["chunks"]

    response = client.post(f"{DOCUMENTS_URL}/{document_id}/embeddings")
    assert response.status_code == 200, response.text
    return {"document_id": document_id, "chunks": chunks}


def _chat(
    client: TestClient,
    question: str,
    *,
    headers: dict[str, str] | None = None,
    **body: object,
) -> dict:
    response = client.post(CHAT_URL, json={"question": question, **body}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["trace_id"] == response.headers["X-Request-ID"]
    return response.json()["data"]


@pytest.fixture
def indexed_client(settings, upload_dir) -> Iterator[tuple[TestClient, dict]]:
    """注入假向量替身,预置一份已入库的员工手册。"""
    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        indexed = _index(
            client,
            "employee.md",
            HANDBOOK,
            tenant="acme",
            department="人力部",
            doc_type="制度",
            version="v1",
        )
        yield client, indexed


def test_chat_returns_grounded_answer_with_full_citations(indexed_client):
    client, indexed = indexed_client

    data = _chat(client, LEAVE_QUESTION)

    assert data["question"] == LEAVE_QUESTION
    assert data["refused"] is False
    assert data["refusal_reason"] is None
    assert "年假" in data["answer"]
    assert data["answer"].startswith("根据知识库资料")

    citations = data["citations"]
    assert len(citations) >= 1
    first = citations[0]
    assert first["index"] == 1
    assert first["chunk_id"] == indexed["chunks"][0]["chunk_id"]
    assert first["document_id"] == indexed["document_id"]
    assert first["file_name"] == "employee.md"
    assert first["title"] == "休假制度"
    assert "page_number" in first
    assert first["score"] > 0
    assert data["latency_ms"] >= 0
    assert data["top_k"] >= 1
    assert data["min_score"] >= 0


def test_chat_refuses_unrelated_question_without_fabricating(indexed_client):
    client, _ = indexed_client

    data = _chat(client, UNRELATED_QUESTION)

    assert data["question"] == UNRELATED_QUESTION
    assert data["refused"] is True
    assert data["refusal_reason"] == "low_score"
    assert data["answer"] == "根据当前资料无法确认。"
    assert data["citations"] == []


def test_query_logs_record_question_hits_and_latency(indexed_client):
    client, _ = indexed_client
    answered = _chat(
        client, LEAVE_QUESTION, headers={"X-Request-ID": "trace-chat-0001"}
    )
    refused = _chat(
        client, UNRELATED_QUESTION, headers={"X-Request-ID": "trace-chat-0002"}
    )

    response = client.get(QUERY_LOGS_URL, params={"limit": 10})

    assert response.status_code == 200
    assert response.json()["trace_id"] == response.headers["X-Request-ID"]
    data = response.json()["data"]
    assert data["total"] == 2
    assert data["limit"] == 10
    assert [item["query_id"] for item in data["items"]] == [
        refused["query_id"],
        answered["query_id"],
    ]

    answered_log = data["items"][1]
    assert answered_log["trace_id"] == "trace-chat-0001"
    assert answered_log["question"] == LEAVE_QUESTION
    assert answered_log["answer"] == answered["answer"]
    assert answered_log["refused"] is False
    assert answered_log["refusal_reason"] is None
    assert answered_log["latency_ms"] >= 0
    assert answered_log["top_k"] == 3
    assert answered_log["min_score"] == 0.35
    assert answered_log["filters"] == {}
    assert answered_log["hits"]
    hit = answered_log["hits"][0]
    assert hit["rank"] == 1
    assert hit["chunk_id"] == answered["citations"][0]["chunk_id"]
    assert hit["score"] > 0
    assert hit["used"] is True

    refused_log = data["items"][0]
    assert refused_log["trace_id"] == "trace-chat-0002"
    assert refused_log["refused"] is True
    assert refused_log["refusal_reason"] == "low_score"
    assert refused_log["latency_ms"] >= 0
    assert refused_log["hits"]
    assert all(hit["used"] is False for hit in refused_log["hits"])

    single = client.get(f"{QUERY_LOGS_URL}/{answered['query_id']}")
    assert single.status_code == 200
    assert single.json()["data"] == answered_log

    missing = client.get(f"{QUERY_LOGS_URL}/no-such-query")
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"
    assert missing.json()["details"] == {"query_id": "no-such-query"}


def test_injected_generator_receives_numbered_context(settings, upload_dir):
    generator = RecordingGenerator()
    app = create_app(
        settings, embedder=DeterministicEmbedder(), answer_generator=generator
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        indexed = _index(
            client,
            "employee.md",
            HANDBOOK,
            tenant="acme",
            department="人力部",
            doc_type="制度",
            version="v1",
        )

        data = _chat(client, LEAVE_QUESTION)

    assert data["answer"] == "根据知识库资料,这是替身回答。"
    assert data["citations"][0]["chunk_id"] == indexed["chunks"][0]["chunk_id"]
    question, context = generator.calls[0]
    assert question == LEAVE_QUESTION
    assert "[1]" in context.text
    assert "文档:employee.md" in context.text
    assert "标题:休假制度" in context.text
    assert "员工入职满一年后,可以申请年假。" in context.text
    assert context.sources[0].citation.chunk_id == indexed["chunks"][0]["chunk_id"]


def test_chat_on_empty_index_refuses_with_no_hits(settings, upload_dir):
    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        data = _chat(client, LEAVE_QUESTION)
        logs = client.get(QUERY_LOGS_URL).json()["data"]

    assert data["refused"] is True
    assert data["refusal_reason"] == "no_hits"
    assert data["answer"] == "根据当前资料无法确认。"
    assert data["citations"] == []
    assert logs["items"][0]["hits"] == []


def test_chat_citation_includes_pdf_page_number(settings, upload_dir):
    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        indexed = _index(
            client,
            "handbook.pdf",
            build_pdf(["Annual leave policy: employees may apply after one year."]),
            tenant="acme",
        )

        data = _chat(client, "annual leave policy")

    assert data["refused"] is False
    assert "Annual leave" in data["answer"]
    citation = data["citations"][0]
    assert citation["chunk_id"] == indexed["chunks"][0]["chunk_id"]
    assert citation["file_name"] == "handbook.pdf"
    assert citation["page_number"] == 1


def test_chat_without_key_returns_unified_upstream_error(client):
    response = client.post(CHAT_URL, json={"question": LEAVE_QUESTION})

    assert response.status_code == 503
    assert response.json()["code"] == "UPSTREAM_NOT_CONFIGURED"
    assert response.json()["details"]["provider"] == "siliconflow"


def test_chat_rejects_blank_question_and_zero_top_k(client):
    response = client.post(CHAT_URL, json={"question": ""})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

    response = client.post(CHAT_URL, json={"question": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

    response = client.post(CHAT_URL, json={"question": LEAVE_QUESTION, "top_k": 0})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_generator_refusal_leaves_no_citations_or_used_hits(settings, upload_dir):
    app = create_app(
        settings,
        embedder=DeterministicEmbedder(),
        answer_generator=RefusingGenerator(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client, "employee.md", HANDBOOK, tenant="acme")

        data = _chat(client, LEAVE_QUESTION)
        log = client.get(f"{QUERY_LOGS_URL}/{data['query_id']}").json()["data"]

    assert data["refused"] is True
    assert data["refusal_reason"] == "generator_refused"
    assert data["answer"] == "根据当前资料无法确认。"
    assert data["citations"] == []
    assert log["hits"]
    assert all(hit["used"] is False for hit in log["hits"])



