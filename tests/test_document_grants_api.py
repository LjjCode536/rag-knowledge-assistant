"""HTTP 主接缝:文档默认不可读与授权清单(#32,ADR-0021)。

验收口径:

- 新文档默认只有所有者与租户管理员可读;
- 用户 / 部门 / 租户三类授权都能让被授权主体检索到文档;
- 撤销授权立即失效答档缓存,不再命中;
- 授权以逻辑文档(租户 + 文件名)为键,重传新版本后继续有效;
- 删除文档连带删除授权,审计事件保留。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.domain.audit import AuditEvent
from app.domain.identity import Principal, PrincipalKind, Role
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from tests.support import ADMIN, MEMBER, authorized_client
from tests.test_embeddings_api import ALPHA_TEXT, _index

DOCUMENTS_URL = "/api/v1/documents"
SEARCH_URL = "/api/v1/search"
CHAT_URL = "/api/v1/chat"
AUDIT_URL = "/api/v1/admin/audit-events"
QUESTION = "休假 工资"

COLLEAGUE = Principal(
    id="usr_colleague",
    kind=PrincipalKind.USER,
    tenant="default",
    role=Role.MEMBER,
    department="研发部",
    username="colleague",
    display_name="同事",
)

MARKETING = Principal(
    id="usr_marketing",
    kind=PrincipalKind.USER,
    tenant="default",
    role=Role.MEMBER,
    department="市场部",
    username="marketing",
    display_name="市场同事",
)


class FailingAuditRepository:
    """审计写入恒失败的替身:验证授权变更被阻断,不留半截状态。"""

    def add(self, event: AuditEvent) -> None:
        raise RuntimeError("audit-store-down")

    def list(self, **kwargs) -> list[AuditEvent]:
        return []

    def count(self, **kwargs) -> int:
        return 0


@pytest.fixture
def grant_clients(
    settings, upload_dir
) -> Iterator[tuple[TestClient, TestClient, TestClient, TestClient]]:
    """所有者、同事、管理员、另一部门同事:同一应用上的真实主体。"""
    app = create_app(settings, embedder=DeterministicEmbedder())
    with (
        authorized_client(app) as owner,
        authorized_client(app, COLLEAGUE) as colleague,
        authorized_client(app, MARKETING) as marketing,
        authorized_client(app, ADMIN.principal) as admin,
    ):
        yield owner, colleague, marketing, admin


def _hits(client: TestClient, question: str = QUESTION) -> set[str]:
    response = client.post(SEARCH_URL, json={"query": question, "top_k": 10})
    assert response.status_code == 200, response.text
    return {
        hit["chunk"]["document_id"] for hit in response.json()["data"]["hits"]
    }


def _grant(
    client: TestClient,
    document_id: str,
    subject_type: str,
    subject_id: str | None = None,
) -> dict:
    response = client.post(
        f"{DOCUMENTS_URL}/{document_id}/grants",
        json={"subject_type": subject_type, "subject_id": subject_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _grants(client: TestClient, document_id: str) -> list[dict]:
    response = client.get(f"{DOCUMENTS_URL}/{document_id}/grants")
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def test_new_document_is_only_readable_by_owner_and_admin(grant_clients) -> None:
    owner, colleague, _, admin = grant_clients
    document = _index(owner, "hr.txt", ALPHA_TEXT)

    assert document["document_id"] in _hits(owner)
    assert document["document_id"] in _hits(admin)
    assert _hits(colleague) == set()
    assert colleague.get(f"{DOCUMENTS_URL}/{document['document_id']}").status_code == 404


def test_user_grant_makes_document_searchable_then_revoke_removes_it(
    grant_clients,
) -> None:
    owner, colleague, _, _ = grant_clients
    document = _index(owner, "hr.txt", ALPHA_TEXT)
    grant = _grant(owner, document["document_id"], "user", COLLEAGUE.id)

    assert document["document_id"] in _hits(colleague)
    # 答档先落缓存,撤销后同题必须重新检索且不再命中
    first = colleague.post(CHAT_URL, json={"question": QUESTION}).json()["data"]
    assert first["cache_hit"] is False
    assert first["citations"]

    revoked = owner.delete(
        f"{DOCUMENTS_URL}/{document['document_id']}/grants/{grant['grant_id']}"
    )
    assert revoked.status_code == 200, revoked.text

    second = colleague.post(CHAT_URL, json={"question": QUESTION}).json()["data"]
    assert second["cache_hit"] is False
    assert second["refused"] is True
    assert _hits(colleague) == set()


def test_department_and_tenant_grants(grant_clients) -> None:
    owner, colleague, marketing, _ = grant_clients
    document = _index(owner, "hr.txt", ALPHA_TEXT)

    _grant(owner, document["document_id"], "department", "研发部")
    assert document["document_id"] in _hits(colleague)
    assert marketing.get(f"{DOCUMENTS_URL}/{document['document_id']}").status_code == 404

    _grant(owner, document["document_id"], "tenant")
    assert document["document_id"] in _hits(marketing)


def test_grant_list_is_owner_or_admin_only(grant_clients) -> None:
    owner, colleague, _, admin = grant_clients
    document = _index(owner, "hr.txt", ALPHA_TEXT)

    assert colleague.get(f"{DOCUMENTS_URL}/{document['document_id']}/grants").status_code == 403
    assert admin.get(f"{DOCUMENTS_URL}/{document['document_id']}/grants").status_code == 200


def test_grant_survives_reupload_of_same_logical_document(grant_clients) -> None:
    owner, colleague, _, _ = grant_clients
    first = _index(owner, "hr.txt", ALPHA_TEXT)
    _grant(owner, first["document_id"], "user", COLLEAGUE.id)

    second = _index(owner, "hr.txt", ALPHA_TEXT)
    assert second["document_id"] != first["document_id"]
    assert second["document_id"] in _hits(colleague)
    assert first["document_id"] not in _hits(colleague)


def test_delete_removes_grants_but_keeps_audit_event(grant_clients) -> None:
    owner, colleague, _, admin = grant_clients
    document = _index(owner, "hr.txt", ALPHA_TEXT)
    _grant(owner, document["document_id"], "user", COLLEAGUE.id)

    deleted = owner.delete(f"{DOCUMENTS_URL}/{document['document_id']}")
    assert deleted.status_code == 200, deleted.text
    assert _grants(owner, document["document_id"]) == []
    assert _hits(colleague) == set()

    audit = admin.get(
        "/api/v1/admin/audit-events", params={"action": "document.delete"}
    )
    assert audit.status_code == 200, audit.text
    events = audit.json()["data"]["items"]
    assert any(
        event["resource_id"] == document["document_id"]
        and event["result"] == "success"
        for event in events
    )
    # 授权记录已删,但删除审计本身仍在
    grant_audit = admin.get(
        "/api/v1/admin/audit-events", params={"action": "document.grant"}
    ).json()["data"]["items"]
    assert any(
        event["resource_id"] == document["document_id"] for event in grant_audit
    )


def test_member_cannot_grant_someone_elses_document(grant_clients) -> None:
    owner, colleague, _, _ = grant_clients
    document = _index(owner, "hr.txt", ALPHA_TEXT)

    response = colleague.post(
        f"{DOCUMENTS_URL}/{document['document_id']}/grants",
        json={"subject_type": "user", "subject_id": COLLEAGUE.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "SCOPE_VIOLATION"
    assert _grants(owner, document["document_id"]) == []


def test_owner_can_publish_and_colleague_keeps_existing_grant_on_admin_reupload(
    grant_clients,
) -> None:
    owner, colleague, _, admin = grant_clients
    document = _index(owner, "hr.txt", ALPHA_TEXT)
    _grant(owner, document["document_id"], "user", COLLEAGUE.id)

    # 管理员代传同名新版本:逻辑文档不变,授权仍生效
    reuploaded = _index(admin, "hr.txt", ALPHA_TEXT)
    assert reuploaded["document_id"] in _hits(colleague)


def test_grant_and_revoke_create_audit_events(grant_clients) -> None:
    """授权与撤销都留下审计事件,细节足以回答"谁把文档给了谁"。"""
    owner, _, _, admin = grant_clients
    document = _index(owner, "hr.txt", ALPHA_TEXT)

    granted = owner.post(
        f"{DOCUMENTS_URL}/{document['document_id']}/grants",
        json={"subject_type": "user", "subject_id": COLLEAGUE.id},
        headers={"X-Request-ID": "trace-grant-0001"},
    )
    assert granted.status_code == 201, granted.text
    grant_id = granted.json()["data"]["grant_id"]

    revoked = owner.delete(
        f"{DOCUMENTS_URL}/{document['document_id']}/grants/{grant_id}",
        headers={"X-Request-ID": "trace-revoke-0001"},
    )
    assert revoked.status_code == 200, revoked.text

    grant_events = admin.get(
        AUDIT_URL, params={"action": "document.grant"}
    ).json()["data"]["items"]
    grant_event = next(
        event for event in grant_events if event["trace_id"] == "trace-grant-0001"
    )
    assert grant_event["tenant"] == "default"
    assert grant_event["principal_id"] == MEMBER.principal.id
    assert grant_event["resource_id"] == document["document_id"]
    assert grant_event["result"] == "success"
    assert grant_event["details"] == {
        "file_name": "hr.txt",
        "subject_type": "user",
        "subject_id": COLLEAGUE.id,
    }

    revoke_events = admin.get(
        AUDIT_URL, params={"action": "document.revoke"}
    ).json()["data"]["items"]
    revoke_event = next(
        event for event in revoke_events if event["trace_id"] == "trace-revoke-0001"
    )
    assert revoke_event["resource_id"] == document["document_id"]
    assert revoke_event["result"] == "success"
    assert revoke_event["details"]["grant_id"] == grant_id
    assert revoke_event["details"]["subject_id"] == COLLEAGUE.id


def test_audit_failure_blocks_grant(settings, upload_dir) -> None:
    """审计写失败即请求失败,授权记录不落库。"""
    app = create_app(
        settings,
        embedder=DeterministicEmbedder(),
        audit_repository=FailingAuditRepository(),
    )
    with authorized_client(app) as owner:
        document = _index(owner, "hr.txt", ALPHA_TEXT)

        response = owner.post(
            f"{DOCUMENTS_URL}/{document['document_id']}/grants",
            json={"subject_type": "user", "subject_id": "usr_anyone"},
        )

        assert response.status_code == 500
        assert response.json()["code"] == "AUDIT_WRITE_FAILED"
        assert _grants(owner, document["document_id"]) == []


def test_department_grant_uses_principal_department_not_document_field(
    grant_clients,
) -> None:
    """部门授权比对主体的部门声明;文档的 ``department`` 只是元数据。"""
    _, colleague, marketing, admin = grant_clients
    document = _index(admin, "hr.txt", ALPHA_TEXT, department="市场部")
    profile = admin.get(f"{DOCUMENTS_URL}/{document['document_id']}").json()["data"]
    assert profile["department"] == "市场部"

    _grant(admin, document["document_id"], "department", "研发部")

    # 市场部同事虽然与文档的 department 一致,但没有得到部门授权
    assert document["document_id"] in _hits(colleague)
    assert document["document_id"] not in _hits(marketing)
