"""HTTP 主接缝:检索与问答只在授权范围内发生(#27)。

验收口径:
- 请求体不再接受租户字段:顶层或 filters 内携带 tenant 一律 422;
- 授权范围由 token 主体构造,跨租户资料在检索与问答里都查不到;
- 收窄超出授权范围(指向另一租户的文档)返回 403 SCOPE_VIOLATION,不静默裁剪;
- 答档缓存按主体与有效范围做键,不跨主体 / 跨租户复用。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.domain.qa import AnswerContext, GenerationUsage, GeneratorKind
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from app.services.generation import GeneratedAnswer
from tests.support import ADMIN, MEMBER, OTHER_TENANT, authorized_client
from tests.test_embeddings_api import ALPHA_TEXT, BETA_TEXT, _chunk, _embed, _upload

DOCUMENTS_URL = "/api/v1/documents"
SEARCH_URL = "/api/v1/search"
CHAT_URL = "/api/v1/chat"

QUESTION = "休假 工资"


@pytest.fixture
def scoped_clients(settings, upload_dir) -> Iterator[tuple[TestClient, TestClient]]:
    """同一应用上的两个真实主体:默认租户 member 与另一租户 member。"""
    app = create_app(settings, embedder=DeterministicEmbedder())
    with (
        authorized_client(app, raise_server_exceptions=False) as default_client,
        authorized_client(
            app, OTHER_TENANT.principal, raise_server_exceptions=False
        ) as other_client,
    ):
        yield default_client, other_client


def _index(client: TestClient, name: str, content: str) -> dict:
    document_id = _upload(client, name, content)["data"]["id"]
    chunks = _chunk(client, document_id)
    assert _embed(client, document_id).status_code == 200
    return {"document_id": document_id, "chunks": chunks}


# ===== 请求契约:租户字段不再由调用方声明 =====


@pytest.mark.parametrize(
    ("url", "field", "value"),
    [(SEARCH_URL, "query", QUESTION), (CHAT_URL, "question", QUESTION)],
)
def test_request_body_rejects_tenant_at_top_level(
    scoped_clients, url: str, field: str, value: str
) -> None:
    client, _ = scoped_clients

    response = client.post(url, json={field: value, "tenant": "other"})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("url", "field", "value"),
    [(SEARCH_URL, "query", QUESTION), (CHAT_URL, "question", QUESTION)],
)
def test_request_narrowing_rejects_tenant_inside_filters(
    scoped_clients, url: str, field: str, value: str
) -> None:
    client, _ = scoped_clients

    response = client.post(url, json={field: value, "filters": {"tenant": "other"}})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("url", "field", "value"),
    [(SEARCH_URL, "query", QUESTION), (CHAT_URL, "question", QUESTION)],
)
def test_search_and_chat_reject_tenant_query_parameter(
    scoped_clients, url: str, field: str, value: str
) -> None:
    """查询参数与请求体同一契约:租户只来自主体,携带即 422。"""
    client, _ = scoped_clients

    response = client.post(f"{url}?tenant=other", json={field: value})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["loc"] == ["query", "tenant"]


# ===== 跨租户不可见:检索与问答都只在 token 的租户内 =====


def test_search_and_chat_only_see_the_principals_tenant(scoped_clients) -> None:
    default_client, other_client = scoped_clients
    mine = _index(default_client, "hr.txt", ALPHA_TEXT)
    theirs = _index(other_client, "hr-other.txt", ALPHA_TEXT)

    search = default_client.post(SEARCH_URL, json={"query": QUESTION, "top_k": 10})
    assert search.status_code == 200, search.text
    data = search.json()["data"]
    assert data["filters"] == {"tenant": "default"}
    assert data["hits"]
    assert {hit["chunk"]["document_id"] for hit in data["hits"]} == {mine["document_id"]}

    chat = default_client.post(CHAT_URL, json={"question": QUESTION})
    assert chat.status_code == 200, chat.text
    assert {citation["document_id"] for citation in chat.json()["data"]["citations"]} == {
        mine["document_id"]
    }

    other_search = other_client.post(SEARCH_URL, json={"query": QUESTION, "top_k": 10})
    assert other_search.status_code == 200, other_search.text
    other_data = other_search.json()["data"]
    assert other_data["filters"] == {"tenant": "other"}
    assert {hit["chunk"]["document_id"] for hit in other_data["hits"]} == {
        theirs["document_id"]
    }

    other_chat = other_client.post(CHAT_URL, json={"question": QUESTION})
    assert other_chat.status_code == 200, other_chat.text
    assert {
        citation["document_id"] for citation in other_chat.json()["data"]["citations"]
    } == {theirs["document_id"]}


def test_tenant_isolation_holds_when_both_tenants_have_the_same_topic(
    scoped_clients,
) -> None:
    """同题诱饵:两租户各有同主题文档,任一方都不得看到对方切片。"""
    default_client, other_client = scoped_clients
    mine = _index(default_client, "handbook.md", ALPHA_TEXT)
    theirs = _index(other_client, "handbook.md", BETA_TEXT)

    default_hits = default_client.post(
        SEARCH_URL, json={"query": "休假 工资 运维", "top_k": 10}
    ).json()["data"]["hits"]
    other_hits = other_client.post(
        SEARCH_URL, json={"query": "休假 工资 运维", "top_k": 10}
    ).json()["data"]["hits"]

    assert theirs["document_id"] not in {
        hit["chunk"]["document_id"] for hit in default_hits
    }
    assert mine["document_id"] not in {
        hit["chunk"]["document_id"] for hit in other_hits
    }


# ===== 收窄越界:403,不静默裁剪 =====


def test_document_id_narrowing_outside_scope_is_rejected(scoped_clients) -> None:
    default_client, other_client = scoped_clients
    mine = _index(default_client, "hr.txt", ALPHA_TEXT)
    theirs = _index(other_client, "hr-other.txt", BETA_TEXT)

    response = default_client.post(
        SEARCH_URL,
        json={"query": QUESTION, "filters": {"document_id": theirs["document_id"]}},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "SCOPE_VIOLATION"

    chat = default_client.post(
        CHAT_URL,
        json={"question": QUESTION, "filters": {"document_id": theirs["document_id"]}},
    )
    assert chat.status_code == 403
    assert chat.json()["code"] == "SCOPE_VIOLATION"

    # 收窄到自己租户的文档仍然有效,回显含服务端补上的 tenant
    allowed = default_client.post(
        SEARCH_URL,
        json={"query": QUESTION, "filters": {"document_id": mine["document_id"]}},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["filters"] == {
        "tenant": "default",
        "document_id": mine["document_id"],
    }


# ===== 答档缓存:不以问题为唯一键 =====


class CountingGenerator:
    """记录生成调用次数,回答固定有据。"""

    kind = GeneratorKind.LLM

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(
        self, question: str, *, context: AnswerContext
    ) -> GeneratedAnswer:
        self.calls.append(question)
        return GeneratedAnswer(
            text="根据知识库资料,这是替身回答。",
            usage=GenerationUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


@pytest.fixture
def cache_app(settings, upload_dir) -> Iterator[tuple[TestClient, CountingGenerator]]:
    generator = CountingGenerator()
    app = create_app(
        settings, embedder=DeterministicEmbedder(), answer_generator=generator
    )
    with authorized_client(app, raise_server_exceptions=False) as client:
        _index(client, "hr.txt", ALPHA_TEXT)
        yield client, generator


def _ask(client: TestClient, principal, question: str = QUESTION) -> dict:
    response = client.post(
        CHAT_URL,
        json={"question": question},
        headers=_authorization(client, principal),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _authorization(client: TestClient, principal) -> dict[str, str]:
    issued = client.app.state.token_service.issue(principal)
    return {"Authorization": f"Bearer {issued.access_token}"}


def test_answer_cache_is_not_shared_across_principals(cache_app) -> None:
    client, generator = cache_app

    first = _ask(client, MEMBER.principal)
    second = _ask(client, ADMIN.principal)
    repeat = _ask(client, MEMBER.principal)

    assert first["cache_hit"] is False
    assert second["cache_hit"] is False  # 同租户不同主体不共享答档
    assert repeat["cache_hit"] is True
    assert len(generator.calls) == 2


def test_answer_cache_is_not_shared_across_tenants(cache_app) -> None:
    client, generator = cache_app

    first = _ask(client, MEMBER.principal)
    other = _ask(client, OTHER_TENANT.principal)
    repeat_mine = _ask(client, MEMBER.principal)

    assert first["cache_hit"] is False
    assert other["cache_hit"] is False  # 不同租户同题不共享答档
    assert repeat_mine["cache_hit"] is True
    assert len(generator.calls) == 1  # 另一租户范围无命中,不触发生成
