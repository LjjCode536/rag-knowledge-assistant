"""HTTP 主接缝:反馈闭环(有用 / 无用反馈、差评汇总与黄金用例候选导出,T11)。

替身上游:沿用确定性假向量替身,问答与反馈全程不触网;
覆盖反馈提交与查询日志关联、按 verdict 过滤、差评候选聚合与结构化导出。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app

DOCUMENTS_URL = "/api/v1/documents"
CHAT_URL = "/api/v1/chat"
FEEDBACK_URL = "/api/v1/feedback"

HANDBOOK = "# 休假制度\n\n员工入职满一年后,可以申请年假。\n"

LEAVE_QUESTION = "入职多久可以申请年假?"
UNRELATED_QUESTION = "垃圾分类怎么处理?"


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
) -> dict:
    response = client.post(CHAT_URL, json={"question": question}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.fixture
def indexed_client(settings, upload_dir) -> Iterator[TestClient]:
    """注入假向量替身,预置一份已入库的员工手册。"""
    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client, "employee.md", HANDBOOK, tenant="acme")
        yield client


def test_feedback_roundtrip_links_query_log_and_trace(indexed_client):
    client = indexed_client

    chat = _chat(client, LEAVE_QUESTION, headers={"X-Request-ID": "trace-fb-0001"})
    response = client.post(
        FEEDBACK_URL,
        json={"query_id": chat["query_id"], "verdict": "useful", "comment": "命中很准"},
        headers={"X-Request-ID": "trace-fb-0002"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["code"] == "OK"
    assert response.json()["trace_id"] == "trace-fb-0002"
    assert response.headers["X-Request-ID"] == "trace-fb-0002"
    feedback = response.json()["data"]

    assert feedback["feedback_id"].startswith("fb_")
    assert feedback["query_id"] == chat["query_id"]
    # trace_id 与查询日志同源:反馈请求自己的 trace_id 只出现在信封里
    assert feedback["trace_id"] == "trace-fb-0001"
    assert feedback["verdict"] == "useful"
    assert feedback["comment"] == "命中很准"
    assert feedback["created_at"]

    single = client.get(f"{FEEDBACK_URL}/{feedback['feedback_id']}")
    assert single.status_code == 200, single.text
    assert single.json()["data"] == feedback

    listing = client.get(FEEDBACK_URL, params={"limit": 10})
    assert listing.status_code == 200
    assert listing.json()["trace_id"] == listing.headers["X-Request-ID"]
    data = listing.json()["data"]
    assert data["total"] == 1
    assert data["limit"] == 10
    assert data["items"] == [feedback]


def test_feedback_useless_with_comment_filters_by_verdict(indexed_client):
    client = indexed_client

    refused = _chat(client, UNRELATED_QUESTION, headers={"X-Request-ID": "trace-fb-0003"})
    response = client.post(
        FEEDBACK_URL,
        json={
            "query_id": refused["query_id"],
            "verdict": "useless",
            "comment": "没有回答我的问题",
        },
    )
    assert response.status_code == 200, response.text
    feedback = response.json()["data"]

    useless = client.get(FEEDBACK_URL, params={"verdict": "useless"})
    assert useless.status_code == 200
    assert useless.json()["data"]["total"] == 1
    assert useless.json()["data"]["items"] == [feedback]

    useful = client.get(FEEDBACK_URL, params={"verdict": "useful"})
    assert useful.status_code == 200
    assert useful.json()["data"]["total"] == 0
    assert useful.json()["data"]["items"] == []


def test_feedback_by_query_id(indexed_client):
    client = indexed_client

    answered = _chat(client, LEAVE_QUESTION)
    refused = _chat(client, UNRELATED_QUESTION)
    client.post(
        FEEDBACK_URL, json={"query_id": refused["query_id"], "verdict": "useless"}
    )

    single = client.get(FEEDBACK_URL, params={"query_id": refused["query_id"]})
    assert single.status_code == 200
    assert single.json()["data"]["total"] == 1
    assert single.json()["data"]["items"][0]["query_id"] == refused["query_id"]

    other = client.get(FEEDBACK_URL, params={"query_id": answered["query_id"]})
    assert other.status_code == 200
    assert other.json()["data"]["total"] == 0
    assert other.json()["data"]["items"] == []


def test_feedback_on_unknown_query_returns_not_found(client):
    response = client.post(
        FEEDBACK_URL, json={"query_id": "no-such-query", "verdict": "useful"}
    )

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"
    assert response.json()["details"] == {"query_id": "no-such-query"}


def test_feedback_rejects_unknown_verdict_and_missing_query_id(client):
    response = client.post(
        FEEDBACK_URL, json={"query_id": "qry_x", "verdict": "maybe"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

    response = client.post(FEEDBACK_URL, json={"verdict": "useful"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_feedback_resubmission_overrides_verdict_of_same_query(indexed_client):
    client = indexed_client

    chat = _chat(client, LEAVE_QUESTION)
    first = client.post(
        FEEDBACK_URL, json={"query_id": chat["query_id"], "verdict": "useful"}
    ).json()["data"]
    second = client.post(
        FEEDBACK_URL,
        json={"query_id": chat["query_id"], "verdict": "useless", "comment": "引用错了"},
    ).json()["data"]

    assert second["feedback_id"] == first["feedback_id"]
    assert second["verdict"] == "useless"
    assert second["comment"] == "引用错了"

    listing = client.get(FEEDBACK_URL)
    assert listing.json()["data"]["total"] == 1
    assert listing.json()["data"]["items"][0]["verdict"] == "useless"


def test_negative_candidates_aggregate_useless_feedback(indexed_client):
    client = indexed_client

    refused = _chat(
        client, UNRELATED_QUESTION, headers={"X-Request-ID": "trace-cand-0001"}
    )
    answered = _chat(
        client, LEAVE_QUESTION, headers={"X-Request-ID": "trace-cand-0002"}
    )
    useful_chat = _chat(client, LEAVE_QUESTION)
    client.post(
        FEEDBACK_URL,
        json={
            "query_id": refused["query_id"],
            "verdict": "useless",
            "comment": "知识库没有这块内容",
        },
    )
    client.post(
        FEEDBACK_URL,
        json={"query_id": answered["query_id"], "verdict": "useless", "comment": "答案太空"},
    )
    client.post(
        FEEDBACK_URL, json={"query_id": useful_chat["query_id"], "verdict": "useful"}
    )

    response = client.get(f"{FEEDBACK_URL}/negative/candidates")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    # 有用反馈不进差评清单;两条差评问题不同 → 两个候选组
    assert data["total"] == 2
    by_question = {item["question"]: item for item in data["items"]}

    refused_candidate = by_question[UNRELATED_QUESTION]
    assert refused_candidate["id"].startswith("cand_")
    assert refused_candidate["occurrences"] == 1
    assert refused_candidate["failure"] == "refusal:low_score"
    assert refused_candidate["comments"] == ["知识库没有这块内容"]
    assert refused_candidate["first_seen"] == refused_candidate["last_seen"]
    origin = refused_candidate["origins"][0]
    assert origin["query_id"] == refused["query_id"]
    assert origin["trace_id"] == "trace-cand-0001"
    assert origin["verdict"] == "useless"
    assert origin["refused"] is True
    assert origin["refusal_reason"] == "low_score"

    answered_candidate = by_question[LEAVE_QUESTION]
    assert answered_candidate["failure"] == "answer_not_useful"
    origin = answered_candidate["origins"][0]
    assert origin["trace_id"] == "trace-cand-0002"
    assert origin["refused"] is False
    assert origin["refusal_reason"] is None
    assert origin["top_documents"]  # 命中来源文档可追溯


def test_negative_candidates_csv_export(indexed_client):
    client = indexed_client

    refused = _chat(client, UNRELATED_QUESTION)
    client.post(
        FEEDBACK_URL,
        json={
            "query_id": refused["query_id"],
            "verdict": "useless",
            "comment": "知识库没有这块内容,搜不到",
        },
    )

    response = client.get(f"{FEEDBACK_URL}/negative/candidates", params={"format": "csv"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    body = response.content.decode("utf-8-sig")
    row = list(csv.reader(io.StringIO(body)))
    # 表头与数据行各自成行:首条候选不得粘在表头同一行(结构化导出回归)
    header = row[0]
    data_rows = row[1:]
    assert len(data_rows) == 1
    assert len(header) == len(data_rows[0]) == 8
    assert body.startswith(
        "id,question,occurrences,failure,first_seen,last_seen,comments,origin_query_ids"
    )
    # UTF-8 BOM:Excel 直接打开中文不乱码
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert data_rows[0][0].startswith("cand_")
    assert data_rows[0][1] == UNRELATED_QUESTION
    assert data_rows[0][3] == "refusal:low_score"
    assert "知识库没有这块内容,搜不到" in data_rows[0][6]


def test_negative_candidates_empty_index(client):
    response = client.get(f"{FEEDBACK_URL}/negative/candidates")

    assert response.status_code == 200
    assert response.json()["data"] == {"limit": 20, "total": 0, "items": []}
