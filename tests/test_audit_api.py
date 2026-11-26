"""HTTP 主接缝:审计事件、平台管理员与公共面收窄(#31)。

验收口径:

- 越权拒绝、跨租户访问、文档删除各产生一条可查审计事件,字段完整;
- 变更类审计失败时请求失败,业务写不执行;拒绝类审计失败不改变响应;
- 审计只读端点仅管理员 / 平台管理员可用,支持按动作与时间过滤;
- 非平台管理员携带租户参数返回 403;平台管理员显式选租户并逐次留痕;
- 公开 ``/health`` 不再暴露缓存与向量身份诊断。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.domain.audit import AuditAction, AuditEvent
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from tests.support import (
    ADMIN,
    MEMBER,
    OTHER_TENANT,
    PLATFORM_OPERATOR,
    authorized_client,
)
from tests.test_embeddings_api import ALPHA_TEXT, _index

DOCUMENTS_URL = "/api/v1/documents"
SEARCH_URL = "/api/v1/search"
AUDIT_URL = "/api/v1/admin/audit-events"
DIAGNOSTICS_URL = "/api/v1/admin/diagnostics"


class FailingAuditRepository:
    """审计写入恒失败的替身:验证变更类操作被阻断、拒绝类响应不受影响。"""

    def add(self, event: AuditEvent) -> None:
        raise RuntimeError("audit-store-down")

    def list(self, **kwargs) -> list[AuditEvent]:
        return []

    def count(self, **kwargs) -> int:
        return 0


@pytest.fixture
def audit_clients(
    settings, upload_dir
) -> Iterator[tuple[TestClient, TestClient, TestClient, TestClient]]:
    app = create_app(settings, embedder=DeterministicEmbedder())
    with (
        authorized_client(app) as member,
        authorized_client(app, ADMIN.principal) as admin,
        authorized_client(app, OTHER_TENANT.principal) as other,
        authorized_client(app, PLATFORM_OPERATOR.principal) as platform,
    ):
        yield member, admin, other, platform


def _events(client: TestClient, **params) -> list[dict]:
    response = client.get(AUDIT_URL, params=params or None)
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def test_denied_narrowing_creates_complete_audit_event(audit_clients) -> None:
    member, admin, other, _ = audit_clients
    theirs = _index(other, "other.txt", ALPHA_TEXT)

    response = member.post(
        SEARCH_URL,
        json={"query": "休假 工资", "filters": {"document_id": theirs["document_id"]}},
        headers={"X-Request-ID": "trace-deny-0001"},
    )

    assert response.status_code == 403
    events = _events(admin, action="access.denied")
    event = next(
        item for item in events if item["resource_id"] == theirs["document_id"]
    )
    assert event["tenant"] == "default"
    assert event["principal_id"] == MEMBER.principal.id
    assert event["action"] == "access.denied"
    assert event["result"] == "denied"
    assert event["resource_type"] == "document"
    assert event["trace_id"] == "trace-deny-0001"
    assert event["occurred_at"]


def test_cross_tenant_object_access_creates_cross_tenant_event(audit_clients) -> None:
    member, admin, other, _ = audit_clients
    theirs = _index(other, "other.txt", ALPHA_TEXT)

    assert member.get(f"{DOCUMENTS_URL}/{theirs['document_id']}").status_code == 404

    events = _events(admin, action="access.cross_tenant")
    event = next(
        item for item in events if item["resource_id"] == theirs["document_id"]
    )
    assert event["result"] == "denied"
    assert event["details"]["reason"] == "cross_tenant"


def test_delete_creates_audit_event_with_resource_and_trace(audit_clients) -> None:
    member, admin, _, _ = audit_clients
    document = _index(member, "hr.txt", ALPHA_TEXT)

    deleted = member.delete(
        f"{DOCUMENTS_URL}/{document['document_id']}",
        headers={"X-Request-ID": "trace-delete-0001"},
    )
    assert deleted.status_code == 200

    events = _events(admin, action="document.delete")
    event = next(
        item for item in events if item["resource_id"] == document["document_id"]
    )
    assert event["tenant"] == "default"
    assert event["principal_id"] == MEMBER.principal.id
    assert event["result"] == "success"
    assert event["trace_id"] == "trace-delete-0001"
    assert event["details"] == {"file_name": "hr.txt"}


def test_audit_endpoint_requires_admin_and_filters(audit_clients) -> None:
    member, admin, _, _ = audit_clients
    document = _index(member, "hr.txt", ALPHA_TEXT)
    member.delete(f"{DOCUMENTS_URL}/{document['document_id']}")

    assert member.get(AUDIT_URL).status_code == 403
    assert member.get(DIAGNOSTICS_URL).status_code == 403

    assert _events(admin, action="document.delete")
    assert _events(admin, action="access.cross_tenant") == []
    # 时间区间过滤:未来终点之前 / 过去终点之后
    assert _events(admin, end="2999-01-01T00:00:00Z")
    assert _events(admin, start="2999-01-01T00:00:00Z") == []
    bad_range = admin.get(
        AUDIT_URL,
        params={
            "start": "2026-01-02T00:00:00Z",
            "end": "2026-01-01T00:00:00Z",
        },
    )
    assert bad_range.status_code == 400


def test_non_platform_admin_tenant_parameter_is_403_and_platform_is_audited(
    audit_clients,
) -> None:
    member, admin, other, platform = audit_clients
    theirs = _index(other, "other.txt", ALPHA_TEXT)

    # 普通成员 / 租户管理员携带 tenant 都是越权(403),不是 422
    assert member.get(DOCUMENTS_URL, params={"tenant": "default"}).status_code == 403
    assert admin.get(DOCUMENTS_URL, params={"tenant": "default"}).status_code == 403
    assert (
        admin.get(AUDIT_URL, params={"tenant": "default"}).status_code == 403
    )

    # 平台管理员必须显式选租户
    assert platform.get(AUDIT_URL).status_code == 400
    cross = platform.get(AUDIT_URL, params={"tenant": "other"})
    assert cross.status_code == 200, cross.text
    # 平台管理员只看到所选租户的事件
    assert {item["tenant"] for item in cross.json()["data"]["items"]} <= {"other"}

    # 跨租户访问逐次留痕:在其它租户的审计里能看到平台访问事件
    platform_events = _events(
        platform, tenant="other", action=AuditAction.PLATFORM_TENANT_ACCESS.value
    )
    assert platform_events
    assert platform_events[0]["resource_id"] == "other"
    assert platform_events[0]["principal_id"] == PLATFORM_OPERATOR.principal.id
    assert theirs["document_id"]


def test_mutation_audit_failure_blocks_delete(settings, upload_dir) -> None:
    app = create_app(
        settings,
        embedder=DeterministicEmbedder(),
        audit_repository=FailingAuditRepository(),
    )
    with authorized_client(app) as member:
        document = _index(member, "hr.txt", ALPHA_TEXT)

        deleted = member.delete(f"{DOCUMENTS_URL}/{document['document_id']}")

        assert deleted.status_code == 500
        assert deleted.json()["code"] == "AUDIT_WRITE_FAILED"
        still_there = member.get(f"{DOCUMENTS_URL}/{document['document_id']}")
        assert still_there.status_code == 200
        assert still_there.json()["data"]["status"] != "deleted"


def test_denial_audit_failure_does_not_change_response(settings, upload_dir) -> None:
    app = create_app(
        settings,
        embedder=DeterministicEmbedder(),
        audit_repository=FailingAuditRepository(),
    )
    with authorized_client(app) as member:
        response = member.get(f"{DOCUMENTS_URL}/no-such-document")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_platform_audit_access_write_failure_fails_request(settings, upload_dir) -> None:
    app = create_app(
        settings,
        embedder=DeterministicEmbedder(),
        audit_repository=FailingAuditRepository(),
    )
    with authorized_client(app, PLATFORM_OPERATOR.principal) as platform:
        response = platform.get(AUDIT_URL, params={"tenant": "other"})

    assert response.status_code == 500
    assert response.json()["code"] == "AUDIT_WRITE_FAILED"


def test_diagnostics_remain_admin_only_after_health_narrowing(audit_clients) -> None:
    member, admin, _, platform = audit_clients

    data = admin.get(DIAGNOSTICS_URL).json()["data"]
    assert data["tenant"] == "default"
    assert "qa_cache" in data
    assert member.get(DIAGNOSTICS_URL).status_code == 403

    cross = platform.get(DIAGNOSTICS_URL, params={"tenant": "other"})
    assert cross.status_code == 200, cross.text
    assert cross.json()["data"]["tenant"] == "other"


def test_audit_time_filter_accepts_iso_datetime(audit_clients) -> None:
    member, admin, _, _ = audit_clients
    document = _index(member, "hr.txt", ALPHA_TEXT)
    member.delete(f"{DOCUMENTS_URL}/{document['document_id']}")

    assert _events(admin, start="2000-01-01T00:00:00Z")
    assert _events(admin, end="2000-01-01T00:00:00Z") == []
