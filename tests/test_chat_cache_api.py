"""HTTP 主接缝:在线问答缓存的命中、口径与失效(T15)。

同一替身注入:相同问题(归一化)二次问答复用答档——生成器只被调用一次,
回答与引用逐字一致,每个请求仍留一条查询日志;文档向量化更新即清缓存。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.domain.qa import AnswerContext, GenerationUsage, GeneratorKind
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from app.services.generation import GeneratedAnswer
from tests.support import json_log_entries

DOCUMENTS_URL = "/api/v1/documents"
CHAT_URL = "/api/v1/chat"
QUERY_LOGS_URL = "/api/v1/query-logs"

HANDBOOK = "# 休假制度\n\n员工入职满一年后,可以申请年假。\n"

LEAVE_QUESTION = "入职多久可以申请年假?"


class ContextEchoGenerator:
    """回答取自上下文标题:知识库更新后同名问题应得到不同回答。"""

    kind = GeneratorKind.LLM

    def __init__(self) -> None:
        self.calls: list[AnswerContext] = []

    async def generate(
        self, question: str, *, context: AnswerContext
    ) -> GeneratedAnswer:
        self.calls.append(context)
        title = context.sources[0].citation.title if context.sources else "无来源"
        return GeneratedAnswer(
            text=f"根据知识库资料,{title}",
            usage=GenerationUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


def _index(
    client: TestClient, name: str, content: str | bytes, *, title: str, **metadata: str
) -> str:
    """上传(可带检索前过滤元数据)→ 切片 → 向量化,返回文档 id。"""
    payload = content.encode() if isinstance(content, str) else content
    response = client.post(
        DOCUMENTS_URL, files={"file": (name, payload, "text/plain")}, data=metadata
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["data"]["id"]

    response = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks", json={"strategy": "heading"}
    )
    assert response.status_code == 200, response.text
    chunks = response.json()["data"]["chunks"]
    assert chunks, "测试文档必须有切片"

    response = client.post(f"{DOCUMENTS_URL}/{document_id}/embeddings")
    assert response.status_code == 200, response.text
    assert chunks[0]["title"] == title
    return document_id


def _chat(
    client: TestClient, question: str, *, headers: dict[str, str] | None = None, **body: object
) -> dict:
    response = client.post(CHAT_URL, json={"question": question, **body}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _cache_client(settings, **create: object) -> Iterator[tuple[TestClient, ContextEchoGenerator]]:
    generator = ContextEchoGenerator()
    app = create_app(
        settings,
        embedder=DeterministicEmbedder(),
        answer_generator=generator,
        **create,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, generator


@pytest.fixture
def indexed(settings, upload_dir) -> Iterator[tuple[TestClient, ContextEchoGenerator]]:
    client, generator = next(_cache_client(settings))
    _index(client, "employee.md", HANDBOOK, title="休假制度")
    yield client, generator


def test_same_question_second_ask_hits_cache(indexed, capsys):
    client, generator = indexed

    first = _chat(client, LEAVE_QUESTION)
    second = _chat(client, f"  {LEAVE_QUESTION}  ")  # 空白归一化后同题

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["answer"] == first["answer"]
    assert second["citations"] == first["citations"]
    assert len(generator.calls) == 1  # 生成链路整段被跳过

    # 命中请求仍各留一条查询日志:可追溯,query_id 不复用
    logs = client.get(QUERY_LOGS_URL, params={"limit": 10}).json()["data"]
    assert logs["total"] == 2
    ids = [item["query_id"] for item in logs["items"]]
    assert len(set(ids)) == 2
    assert all(item["answer"] == first["answer"] for item in logs["items"])
    # 留痕以本次请求为准:命中日志记调用方的原文,而不是答档的首次问题
    assert logs["items"][0]["question"] == f"  {LEAVE_QUESTION}  "

    # 命中 / 未命中进结构化日志,命中率可从事件序列求得
    entries = json_log_entries(capsys.readouterr().out)
    events = [entry["event"] for entry in entries if entry["event"].startswith("qa_cache_")]
    assert events == ["qa_cache_miss", "qa_cache_hit"]
    hit = next(
        entry for entry in entries if entry["event"] == "qa_cache_hit"
    )
    assert hit["query_id"] == ids[0]  # items 最近在前
    assert hit["query_id"] == second["query_id"]
    assert hit["cache_hit_rate"] == 0.5


def test_cache_disabled_asks_fresh_every_time(settings, upload_dir):
    configured = settings.model_copy(update={"qa_cache_enabled": False})
    for client, generator in _cache_client(configured):
        _index(client, "employee.md", HANDBOOK, title="休假制度")

        _chat(client, LEAVE_QUESTION)
        _chat(client, LEAVE_QUESTION)

        assert len(generator.calls) == 2


def test_cache_key_respects_top_k_and_filters(settings, upload_dir):
    for client, generator in _cache_client(settings):
        _index(client, "employee.md", HANDBOOK, title="休假制度", department="人力部")

        _chat(client, LEAVE_QUESTION)
        _chat(client, LEAVE_QUESTION, top_k=1)
        _chat(client, LEAVE_QUESTION, top_k=1, filters={"department": "人力部"})
        _chat(client, LEAVE_QUESTION, top_k=1)
        _chat(client, LEAVE_QUESTION, top_k=3)

        assert len(generator.calls) == 3  # 同题不同口径不共享答档


def test_new_document_invalidation_forces_fresh_answer(settings, upload_dir):
    for client, generator in _cache_client(settings):
        _index(client, "employee.md", HANDBOOK, title="休假制度")

        _chat(client, LEAVE_QUESTION)
        assert _chat(client, LEAVE_QUESTION)["cache_hit"] is True
        assert len(generator.calls) == 1

        # 新文档入库(向量化):检索面变化,缓存整体失效,同题重新检索生成
        _index(
            client,
            "annual_leave.md",
            "# 年假新规\n\n年假上限调整为十五天。\n",
            title="年假新规",
        )
        refreshed = _chat(client, LEAVE_QUESTION)

        assert len(generator.calls) == 2
        assert refreshed["cache_hit"] is False


def test_document_deletion_forces_fresh_refusal(settings, upload_dir):
    from app.domain.qa import REFUSAL_ANSWER

    for client, _ in _cache_client(settings):
        document_id = _index(client, "employee.md", HANDBOOK, title="休假制度")

        grounded = _chat(client, LEAVE_QUESTION)
        assert grounded["cache_hit"] is False
        assert grounded["refused"] is False

        response = client.delete(f"{DOCUMENTS_URL}/{document_id}")
        assert response.status_code == 200, response.text

        refreshed = _chat(client, LEAVE_QUESTION)

        assert refreshed["cache_hit"] is False  # 删除退出了检索面,旧答档不再复用
        assert refreshed["refused"] is True
        assert refreshed["answer"] == REFUSAL_ANSWER


def test_new_version_supersede_forces_fresh_refusal(settings, upload_dir):
    from app.domain.qa import REFUSAL_ANSWER

    for client, _ in _cache_client(settings):
        _index(client, "employee.md", HANDBOOK, title="休假制度")

        grounded = _chat(client, LEAVE_QUESTION)
        assert grounded["cache_hit"] is False
        assert grounded["refused"] is False

        new_content = "# 休假制度 v2\n\n年假政策整体作废。\n"
        response = client.post(
            DOCUMENTS_URL,
            files={"file": ("employee.md", new_content.encode(), "text/plain")},
        )
        assert response.status_code == 201, response.text

        refreshed = _chat(client, LEAVE_QUESTION)

        assert refreshed["cache_hit"] is False  # 旧版本退位,旧答档不再复用
        assert refreshed["refused"] is True
        assert refreshed["answer"] == REFUSAL_ANSWER
