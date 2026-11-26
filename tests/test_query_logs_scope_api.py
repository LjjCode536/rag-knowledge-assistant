"""HTTP 主接缝:查询日志、反馈与统计按租户 / 主体收敛(#30)。

验收口径:

- 查询日志与反馈带显式租户与主体,响应可核对;
- 成员只读自己的日志与反馈,管理员读本租户全部,跨租户 404;
- 成员只能用**自己的** query_id 提交反馈;
- 统计、成本与差评候选只统计本租户(成员再收到自己的主体范围);
- 答档命中仍各留一条日志,租户与主体按本次请求写。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.domain.identity import Principal, PrincipalKind, Role
from app.domain.qa import AnswerContext, GenerationUsage, GeneratorKind
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from app.services.generation import GeneratedAnswer
from tests.support import ADMIN, MEMBER, OTHER_TENANT, authorized_client
from tests.test_embeddings_api import ALPHA_TEXT, _index

DOCUMENTS_URL = "/api/v1/documents"
CHAT_URL = "/api/v1/chat"
QUERY_LOGS_URL = "/api/v1/query-logs"
FEEDBACK_URL = "/api/v1/feedback"
STATS_URL = "/api/v1/stats"

QUESTION = "入职多久可以申请年假?"

COLLEAGUE = Principal(
    id="usr_colleague",
    kind=PrincipalKind.USER,
    tenant="default",
    role=Role.MEMBER,
    department="研发部",
    username="colleague",
    display_name="同事",
)


class UsageGenerator:
    """固定 Token 用量的生成器替身,让成本口径可精确断言。"""

    kind = GeneratorKind.LLM

    async def generate(
        self, question: str, *, context: AnswerContext
    ) -> GeneratedAnswer:
        return GeneratedAnswer(
            text="员工入职满一年后可以申请年假。",
            usage=GenerationUsage(
                prompt_tokens=100, completion_tokens=50, total_tokens=150
            ),
        )


@pytest.fixture
def scoped_clients(
    settings, upload_dir
) -> Iterator[tuple[TestClient, TestClient, TestClient, TestClient]]:
    """同一应用上的四个真实主体:成员、同租户同事、管理员、另一租户成员。"""
    app = create_app(
        settings,
        embedder=DeterministicEmbedder(),
        answer_generator=UsageGenerator(),
    )
    with (
        authorized_client(app, raise_server_exceptions=False) as member,
        authorized_client(app, COLLEAGUE, raise_server_exceptions=False) as colleague,
        authorized_client(app, ADMIN.principal, raise_server_exceptions=False) as admin,
        authorized_client(
            app, OTHER_TENANT.principal, raise_server_exceptions=False
        ) as other,
    ):
        yield member, colleague, admin, other


def _chat(client: TestClient, question: str = QUESTION) -> dict:
    response = client.post(CHAT_URL, json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _feedback(client: TestClient, query_id: str, verdict: str, comment: str | None = None):
    return client.post(
        FEEDBACK_URL,
        json={"query_id": query_id, "verdict": verdict, "comment": comment},
    )


# ===== 查询日志:主体 / 租户显式,成员只看自己 =====


def test_query_logs_carry_tenant_and_principal_and_are_principal_scoped(
    scoped_clients,
):
    member, colleague, admin, other = scoped_clients

    mine = _chat(member)
    theirs = _chat(colleague, "垃圾分类怎么处理?")
    admin_log = _chat(admin, "年假有几天?")

    listing = member.get(QUERY_LOGS_URL).json()["data"]
    assert listing["total"] == 1
    (item,) = listing["items"]
    assert item["query_id"] == mine["query_id"]
    assert (item["tenant"], item["principal_id"]) == (
        "default",
        MEMBER.principal.id,
    )

    # 成员读同事 / 管理员的日志:404;管理员读本租户全部
    for query_id in (theirs["query_id"], admin_log["query_id"]):
        blocked = member.get(f"{QUERY_LOGS_URL}/{query_id}")
        assert blocked.status_code == 404, blocked.text
        assert blocked.json()["code"] == "NOT_FOUND"

    assert admin.get(f"{QUERY_LOGS_URL}/{theirs['query_id']}").status_code == 200
    admin_listing = admin.get(QUERY_LOGS_URL).json()["data"]
    assert admin_listing["total"] == 3
    assert {entry["principal_id"] for entry in admin_listing["items"]} == {
        MEMBER.principal.id,
        COLLEAGUE.id,
        ADMIN.principal.id,
    }
    assert {entry["tenant"] for entry in admin_listing["items"]} == {"default"}

    # 跨租户:既不在清单里,按 id 也 404
    assert other.get(QUERY_LOGS_URL).json()["data"]["total"] == 0
    assert other.get(f"{QUERY_LOGS_URL}/{mine['query_id']}").status_code == 404


# ===== 反馈:只能挂自己的问答,读路径同样按主体收敛 =====


def test_feedback_submission_and_reads_are_principal_scoped(scoped_clients):
    member, colleague, admin, other = scoped_clients

    mine = _chat(member, "入职多久可以申请年假?")
    theirs = _chat(colleague, "垃圾分类怎么处理?")

    # 用他人 query_id 提交反馈:404,不生成反馈
    blocked = _feedback(member, theirs["query_id"], "useless")
    assert blocked.status_code == 404, blocked.text
    assert member.get(FEEDBACK_URL).json()["data"]["total"] == 0

    created = _feedback(member, mine["query_id"], "useless", "没有回答我的问题")
    assert created.status_code == 200, created.text
    feedback = created.json()["data"]
    assert (feedback["tenant"], feedback["principal_id"]) == (
        "default",
        MEMBER.principal.id,
    )

    # 同事 / 异租户读不到;管理员读得到本租户全部
    assert (
        colleague.get(f"{FEEDBACK_URL}/{feedback['feedback_id']}").status_code == 404
    )
    assert other.get(f"{FEEDBACK_URL}/{feedback['feedback_id']}").status_code == 404
    assert admin.get(f"{FEEDBACK_URL}/{feedback['feedback_id']}").status_code == 200

    colleague_feedback = _feedback(
        colleague, theirs["query_id"], "useful"
    ).json()["data"]
    colleague_listing = colleague.get(FEEDBACK_URL).json()["data"]
    assert colleague_listing["total"] == 1
    assert colleague_listing["items"][0]["feedback_id"] == colleague_feedback["feedback_id"]

    admin_listing = admin.get(FEEDBACK_URL).json()["data"]
    assert admin_listing["total"] == 2
    assert {item["principal_id"] for item in admin_listing["items"]} == {
        MEMBER.principal.id,
        COLLEAGUE.id,
    }

    # 成员按 query_id 反查他人反馈:不可见(空),不泄露
    assert (
        member.get(FEEDBACK_URL, params={"query_id": theirs["query_id"]})
        .json()["data"]["total"]
        == 0
    )


# ===== 统计、成本与差评候选:只统计本租户 =====


def test_stats_and_cost_only_count_the_principals_tenant(scoped_clients):
    member, _, _, other = scoped_clients

    _index(member, "default.md", ALPHA_TEXT)
    _index(other, "other.md", ALPHA_TEXT)
    _chat(member, "休假 工资")
    _chat(other, "休假 工资")

    member_stats = member.get(STATS_URL).json()["data"]
    other_stats = other.get(STATS_URL).json()["data"]

    assert member_stats["document_count"] == 1
    assert member_stats["indexed_document_count"] == 1
    assert member_stats["qa"]["query_count"] == 1
    assert member_stats["qa"]["token_usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    }
    assert other_stats["qa"]["query_count"] == 1
    assert other_stats["document_count"] == 1

    member_cost = member.get(f"{STATS_URL}/cost").json()["data"]
    assert member_cost["token_usage"]["prompt_tokens"] == 100
    assert member_cost["token_usage"]["completion_tokens"] == 50


def test_negative_candidates_only_include_own_tenant_and_member_scope(scoped_clients):
    member, colleague, admin, other = scoped_clients

    mine = _chat(member)
    theirs = _chat(colleague, "垃圾分类怎么处理?")
    admin_log = _chat(admin, "年假有几天?")
    other_log = _chat(other, "外地办公怎么申请?")

    assert _feedback(member, mine["query_id"], "useless").status_code == 200
    assert _feedback(colleague, theirs["query_id"], "useless").status_code == 200
    assert _feedback(admin, admin_log["query_id"], "useless").status_code == 200
    assert _feedback(other, other_log["query_id"], "useless").status_code == 200

    member_candidates = member.get(f"{FEEDBACK_URL}/negative/candidates").json()["data"]
    assert member_candidates["total"] == 1
    assert [item["question"] for item in member_candidates["items"]] == [QUESTION]

    admin_candidates = admin.get(f"{FEEDBACK_URL}/negative/candidates").json()["data"]
    assert admin_candidates["total"] == 3
    assert {item["question"] for item in admin_candidates["items"]} == {
        QUESTION,
        "垃圾分类怎么处理?",
        "年假有几天?",
    }

    other_candidates = other.get(f"{FEEDBACK_URL}/negative/candidates").json()["data"]
    assert other_candidates["total"] == 1
    assert [item["question"] for item in other_candidates["items"]] == [
        "外地办公怎么申请?"
    ]


# ===== 答档命中:各留一条日志,主体 / 租户按本次请求写 =====


def test_cache_hit_still_logs_each_answer_with_correct_principal_and_tenant(
    scoped_clients,
):
    member, _, admin, other = scoped_clients
    _index(member, "default.md", ALPHA_TEXT)

    first = _chat(member)
    second = _chat(member)

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True

    listing = member.get(QUERY_LOGS_URL).json()["data"]
    assert listing["total"] == 2
    assert {entry["query_id"] for entry in listing["items"]} == {
        first["query_id"],
        second["query_id"],
    }
    assert {
        (entry["tenant"], entry["principal_id"]) for entry in listing["items"]
    } == {("default", MEMBER.principal.id)}

    # 管理员看到同一租户的两条;异租户一条也看不到
    assert admin.get(QUERY_LOGS_URL).json()["data"]["total"] == 2
    assert other.get(QUERY_LOGS_URL).json()["data"]["total"] == 0
