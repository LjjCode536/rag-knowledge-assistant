"""HTTP 主接缝:配置切换 rule / llm 生成器与上游错误码(不触网)。

验收口径(T08):
- 默认 qa_generator=llm:无密钥时 /chat 直接 503 UPSTREAM_NOT_CONFIGURED;
- keyed + 假传输层注入 LLM 生成器:有据回答、完整引用、查询日志含
  生成器种类 / Token 用量;模型拒答 → generator_refused;
- 上游超时 / 限流在 API 缝转为统一错误码;
- 改写 / 扩展后的问题随查询日志留痕。
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.retrieval import QueryPlan
from app.integrations.llm.client import UnifiedLLMClient
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.integrations.llm.transport import TransportResponse
from app.main import create_app
from app.services.generation import SiliconFlowAnswerGenerator
from tests.test_llm_client import FakeTransport

CHAT_URL = "/api/v1/chat"
QUERY_LOGS_URL = "/api/v1/query-logs"

HANDBOOK = "# 休假制度\n\n员工入职满一年后,可以申请年假。\n"
LEAVE_QUESTION = "入职多久可以申请年假?"

LLM_ANSWER_BODY = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "根据 [1],员工入职满一年后即可申请年假。",
            }
        }
    ],
    "usage": {"prompt_tokens": 88, "completion_tokens": 12, "total_tokens": 100},
}

LLM_REFUSAL_BODY = {
    "choices": [
        {"message": {"role": "assistant", "content": "根据当前资料无法确认。"}}
    ],
    "usage": {"prompt_tokens": 66, "completion_tokens": 2, "total_tokens": 68},
}


def _index(client: TestClient) -> dict:
    payload = HANDBOOK.encode()
    response = client.post(
        "/api/v1/documents",
        files={"file": ("employee.md", payload, "text/plain")},
        data={"tenant": "acme"},
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["data"]["id"]

    response = client.post(
        f"/api/v1/documents/{document_id}/chunks", json={"strategy": "heading"}
    )
    assert response.status_code == 200, response.text

    response = client.post(f"/api/v1/documents/{document_id}/embeddings")
    assert response.status_code == 200, response.text
    return {"document_id": document_id}


def _chat(client: TestClient, question: str) -> dict:
    response = client.post(CHAT_URL, json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()["data"]


class StubQueryPlanner:
    """查询规划替身:固定产出改写与变体,不触上游。"""

    def __init__(self, plan: QueryPlan) -> None:
        self._plan = plan

    async def plan(self, question: str, *, variant_count: int) -> QueryPlan:
        return self._plan


def _llm_settings(settings: Settings, **updates: object) -> Settings:
    """LLM 生成模式的离线配置:有密钥、走替身、关掉其它上游消费开关。"""
    return settings.model_copy(
        update={
            "siliconflow_api_key": "k-test",
            "llm_max_retries": 0,
            "use_query_rewrite": False,
            "use_rerank": False,
            "qa_generator": "llm",
            **updates,
        }
    )


def _llm_client(
    settings: Settings, responses: list[TransportResponse | Exception]
) -> tuple[UnifiedLLMClient, FakeTransport]:
    transport = FakeTransport(responses)
    return UnifiedLLMClient(settings, transport=transport), transport


@pytest.fixture
def llm_indexed_client(settings: Settings) -> Iterator[tuple[TestClient, FakeTransport]]:
    """注入假传输层驱动的真实 LLM 生成器,预置已入库手册。"""
    app_settings = _llm_settings(settings)
    llm_client, transport = _llm_client(app_settings, [TransportResponse(200, LLM_ANSWER_BODY)])
    app = create_app(
        app_settings,
        embedder=DeterministicEmbedder(),
        answer_generator=SiliconFlowAnswerGenerator(llm_client),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client)
        yield client, transport


def test_default_qa_generator_is_llm_and_surfaces_unified_error(upload_dir) -> None:
    """默认配置即真实 LLM:无密钥时 /chat 转 UPSTREAM_NOT_CONFIGURED 错误码。"""
    settings = Settings(_env_file=None, environment="test", upload_dir=upload_dir)
    assert settings.qa_generator == "llm"

    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client)
        response = client.post(CHAT_URL, json={"question": LEAVE_QUESTION})

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "UPSTREAM_NOT_CONFIGURED"


def test_llm_generation_grounds_answer_and_logs_usage(llm_indexed_client) -> None:
    client, transport = llm_indexed_client

    data = _chat(client, LEAVE_QUESTION)

    assert data["refused"] is False
    assert data["answer"] == "根据 [1],员工入职满一年后即可申请年假。"
    assert data["generator"] == "llm"
    assert data["token_usage"]["total_tokens"] == 100
    assert len(transport.calls) == 1

    citations = data["citations"]
    assert len(citations) == 1
    assert citations[0]["file_name"] == "employee.md"

    log = client.get(f"{QUERY_LOGS_URL}/{data['query_id']}").json()["data"]
    assert log["generator"] == "llm"
    assert log["token_usage"]["total_tokens"] == 100
    assert log["hits"][0]["used"] is True


def test_llm_refusal_on_insufficient_context_is_stable(
    settings: Settings, upload_dir
) -> None:
    """模型对知识库范围外的问题按 Prompt 契约原样拒答。

    阈值降为 0 让无关问题也能进上下文,单独验证"生成器兜底拒答"这一层。
    """
    app_settings = _llm_settings(settings, qa_min_score=0.0)
    llm_client, _ = _llm_client(app_settings, [TransportResponse(200, LLM_REFUSAL_BODY)])
    app = create_app(
        app_settings,
        embedder=DeterministicEmbedder(),
        answer_generator=SiliconFlowAnswerGenerator(llm_client),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client)

        data = _chat(client, "公司健身房开放到几点?")

    assert data["refused"] is True
    assert data["refusal_reason"] == "generator_refused"
    assert data["answer"] == "根据当前资料无法确认。"
    assert data["citations"] == []

    log = client.get(f"{QUERY_LOGS_URL}/{data['query_id']}").json()["data"]
    assert log["token_usage"]["total_tokens"] == 68
    assert all(hit["used"] is False for hit in log["hits"])


@pytest.mark.parametrize("failure,status_code", [
    (httpx.ReadTimeout("slow llm"), 504),
    (TransportResponse(429, {}), 429),
])
def test_llm_upstream_becomes_unified_error_codes_at_api_seam(
    settings, upload_dir, failure, status_code
) -> None:
    """生成步的上游超时 / 限流经主缝转为统一错误码。"""
    api_codes = {504: "UPSTREAM_TIMEOUT", 429: "UPSTREAM_RATE_LIMITED"}
    app_settings = _llm_settings(settings)
    llm_client, _ = _llm_client(app_settings, [failure])
    app = create_app(
        app_settings,
        embedder=DeterministicEmbedder(),
        answer_generator=SiliconFlowAnswerGenerator(llm_client),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client)
        response = client.post(CHAT_URL, json={"question": LEAVE_QUESTION})

    assert response.status_code == status_code, response.text
    assert response.json()["code"] == api_codes[status_code]


def test_query_log_records_rewritten_and_expanded_questions(
    settings: Settings, upload_dir
) -> None:
    """改写 / 扩展口径随查询日志留痕:开关谁开谁生效。"""
    app_settings = _llm_settings(
        settings, qa_generator="rule", use_query_rewrite=True, use_multi_query=True
    )
    plan = QueryPlan(
        original=LEAVE_QUESTION,
        rewritten="年假申请条件",
        variants=["入职满一年休年假"],
    )
    app = create_app(
        app_settings,
        embedder=DeterministicEmbedder(),
        query_planner=StubQueryPlanner(plan),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client)
        data = _chat(client, LEAVE_QUESTION)
        log = client.get(f"{QUERY_LOGS_URL}/{data['query_id']}").json()["data"]

    assert log["rewritten_question"] == "年假申请条件"
    assert log["expanded_questions"] == ["入职满一年休年假"]


def test_query_log_keeps_original_when_rewrite_disabled(
    settings: Settings, upload_dir
) -> None:
    """改写开关关闭时,规划产物不写进日志:只增不减的口径。"""
    app_settings = _llm_settings(
        settings, qa_generator="rule", use_query_rewrite=False, use_multi_query=True
    )
    plan = QueryPlan(original=LEAVE_QUESTION, rewritten="年假申请条件", variants=["变体"])
    app = create_app(
        app_settings,
        embedder=DeterministicEmbedder(),
        query_planner=StubQueryPlanner(plan),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client)
        data = _chat(client, LEAVE_QUESTION)
        log = client.get(f"{QUERY_LOGS_URL}/{data['query_id']}").json()["data"]

    assert log["rewritten_question"] is None
    assert log["expanded_questions"] == ["变体"]
