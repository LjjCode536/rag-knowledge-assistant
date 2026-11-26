"""HTTP 主接缝:文档写路径与文档所有者(#29)。

验收口径:

- 上传不再接受租户;租户与所有者来自主体,部门缺省取主体部门,管理员可指定;
- 成员不能把文档写到别人的部门;非所有者成员切分 / 向量化 / 删除他人文档 404;
- 成员重传他人同名文档 404;所有者重传时所有者不变,管理员代传也不改变归属;
- 管理员可管理本租户全部文档。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.domain.identity import Principal, PrincipalKind, Role
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from tests.support import ADMIN, MEMBER, OTHER_TENANT, authorized_client

DOCUMENTS_URL = "/api/v1/documents"

COLLEAGUE = Principal(
    id="usr_colleague",
    kind=PrincipalKind.USER,
    tenant="default",
    role=Role.MEMBER,
    department="研发部",
    username="colleague",
    display_name="同事",
)


@pytest.fixture
def write_clients(
    settings, upload_dir
) -> Iterator[tuple[TestClient, TestClient, TestClient, TestClient]]:
    """同一应用上的四个真实主体:所有者、同租户同事、管理员、另一租户成员。"""
    app = create_app(settings, embedder=DeterministicEmbedder())
    with (
        authorized_client(app, raise_server_exceptions=False) as owner,
        authorized_client(app, COLLEAGUE, raise_server_exceptions=False) as colleague,
        authorized_client(app, ADMIN.principal, raise_server_exceptions=False) as admin,
        authorized_client(
            app, OTHER_TENANT.principal, raise_server_exceptions=False
        ) as other,
    ):
        yield owner, colleague, admin, other


def _upload(client: TestClient, name: str, **form: str) -> dict:
    response = client.post(
        DOCUMENTS_URL,
        files={"file": (name, "# 手册\n\n正文内容。\n".encode(), "text/markdown")},
        data=form or None,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_upload_derives_tenant_owner_and_department_from_principal(write_clients):
    owner, _, _, _ = write_clients

    document = _upload(owner, "handbook.md")

    assert document["tenant"] == "default"
    assert document["owner_id"] == MEMBER.principal.id
    assert document["department"] == MEMBER.principal.department


def test_upload_rejects_tenant_form_field(write_clients):
    owner, _, _, _ = write_clients

    response = owner.post(
        DOCUMENTS_URL,
        files={"file": ("handbook.md", "正文".encode(), "text/markdown")},
        data={"tenant": "other"},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["loc"] == ["body", "tenant"]
    # 被拒绝的请求不留下档案
    assert owner.get(DOCUMENTS_URL).json()["data"]["total"] == 0


def test_member_cannot_choose_another_department_but_admin_can(write_clients):
    owner, _, admin, _ = write_clients

    rejected = owner.post(
        DOCUMENTS_URL,
        files={"file": ("other-dept.md", "正文".encode(), "text/markdown")},
        data={"department": "人力部"},
    )
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["code"] == "SCOPE_VIOLATION"

    same = _upload(owner, "same-dept.md", department=MEMBER.principal.department)
    assert same["department"] == MEMBER.principal.department

    by_admin = _upload(admin, "admin-dept.md", department="人力部")
    assert by_admin["department"] == "人力部"
    assert by_admin["owner_id"] == ADMIN.principal.id


def test_non_owner_member_cannot_chunk_embed_or_delete_and_admin_can(write_clients):
    owner, colleague, admin, other = write_clients
    document_id = _upload(owner, "handbook.md")["id"]

    for client in (colleague, other):
        for suffix, body in (
            ("/chunks", None),
            ("/embeddings", None),
            ("", None),
        ):
            method = client.delete if not suffix else client.post
            kwargs = {"json": body} if suffix == "/chunks" else {}
            response = method(f"{DOCUMENTS_URL}/{document_id}{suffix}", **kwargs)
            assert response.status_code == 404, (client, suffix, response.text)
            assert response.json()["code"] == "NOT_FOUND"

    # 管理员可管理本租户全部文档:切分 → 向量化 → 删除
    assert (
        admin.post(f"{DOCUMENTS_URL}/{document_id}/chunks").status_code == 200
    )
    assert (
        admin.post(f"{DOCUMENTS_URL}/{document_id}/embeddings").status_code == 200
    )
    deleted = admin.delete(f"{DOCUMENTS_URL}/{document_id}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["status"] == "deleted"


def test_member_reupload_of_another_members_document_is_404(write_clients):
    owner, colleague, _, _ = write_clients
    original = _upload(owner, "handbook.md")

    response = colleague.post(
        DOCUMENTS_URL,
        files={"file": ("handbook.md", "新版本正文".encode(), "text/markdown")},
    )

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"
    # 旧版本仍是现行版本,没有被同事的重传挤掉
    latest = owner.get(f"{DOCUMENTS_URL}/{original['id']}").json()["data"]
    assert latest["is_latest"] is True
    assert owner.get(DOCUMENTS_URL).json()["data"]["total"] == 1


def test_owner_reupload_keeps_owner_and_admin_reupload_keeps_existing_owner(
    write_clients,
):
    owner, _, admin, _ = write_clients
    first = _upload(owner, "handbook.md")

    second = _upload(owner, "handbook.md", version="v2")
    assert second["id"] != first["id"]
    assert second["owner_id"] == first["owner_id"] == MEMBER.principal.id

    third = _upload(admin, "handbook.md", version="v3")
    assert third["id"] != second["id"]
    # 管理员代传新版本不把所有者改成自己
    assert third["owner_id"] == MEMBER.principal.id

    history = owner.get(DOCUMENTS_URL, params={"include_history": True})
    assert history.status_code == 200, history.text
    owners = {
        item["owner_id"] for item in history.json()["data"]["items"]
    }
    assert owners == {MEMBER.principal.id}
