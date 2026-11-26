"""HTTP 主接缝:文档读路径按主体租户收敛(#28)。

验收口径:

- 清单不再接受租户查询参数:携带 ``tenant`` 即 422,走统一信封;
- 清单只返回主体租户的知识库文档,``total`` 与清单同口径收缩;
- 跨租户 id 的详情 / 解析产物 / 切片一律 404,与不存在对象同一入口,不泄露存在性;
- 管理员与成员本片都看本租户全部文档(文档授权落地后再收紧)。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.support import ADMIN, OTHER_TENANT, authorized_client

DOCUMENTS_URL = "/api/v1/documents"


@pytest.fixture
def tenant_clients(
    settings, upload_dir
) -> Iterator[tuple[TestClient, TestClient, TestClient]]:
    """同一应用上的三个真实主体:本租户成员、本租户管理员、另一租户成员。"""
    app = create_app(settings)
    with (
        authorized_client(app, raise_server_exceptions=False) as member_client,
        authorized_client(
            app, ADMIN.principal, raise_server_exceptions=False
        ) as admin_client,
        authorized_client(
            app, OTHER_TENANT.principal, raise_server_exceptions=False
        ) as other_client,
    ):
        yield member_client, admin_client, other_client


def _upload(client: TestClient, name: str) -> dict:
    """上传一份知识库文档;租户来自客户端携带的真实 JWT。"""
    response = client.post(
        DOCUMENTS_URL,
        files={"file": (name, "正文内容。".encode(), "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


# ===== 清单:只列本租户,总数同步收缩 =====


def test_list_only_returns_principal_tenant_and_total_shrinks(tenant_clients) -> None:
    member, _, other = tenant_clients
    mine = _upload(member, "mine.md")
    also_mine = _upload(member, "also-mine.md")
    theirs = _upload(other, "theirs.md")

    data = member.get(DOCUMENTS_URL).json()["data"]
    assert data["total"] == 2
    assert {item["id"] for item in data["items"]} == {mine["id"], also_mine["id"]}
    assert theirs["id"] not in {item["id"] for item in data["items"]}

    other_data = other.get(DOCUMENTS_URL).json()["data"]
    assert other_data["total"] == 1
    assert [item["id"] for item in other_data["items"]] == [theirs["id"]]


@pytest.mark.parametrize("tenant", ["default", "other", "nobody"])
def test_list_rejects_tenant_query_parameter(tenant_clients, tenant: str) -> None:
    member, _, _ = tenant_clients

    response = member.get(DOCUMENTS_URL, params={"tenant": tenant})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["trace_id"] == response.headers["X-Request-ID"]
    assert body["details"]["errors"][0]["loc"] == ["query", "tenant"]


@pytest.mark.parametrize("suffix", ["", "/elements", "/chunks"])
def test_document_read_rejects_tenant_query_parameter(
    tenant_clients, suffix: str
) -> None:
    member, _, _ = tenant_clients
    mine = _upload(member, "mine.md")

    response = member.get(
        f"{DOCUMENTS_URL}/{mine['id']}{suffix}", params={"tenant": "other"}
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["loc"] == ["query", "tenant"]


# ===== 对象级读路径:跨租户 404,与不存在同口径 =====


@pytest.mark.parametrize("suffix", ["", "/elements", "/chunks"])
def test_cross_tenant_document_read_is_404(tenant_clients, suffix: str) -> None:
    member, _, other = tenant_clients
    theirs = _upload(other, "theirs.md")

    response = member.get(f"{DOCUMENTS_URL}/{theirs['id']}{suffix}")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["details"] == {"document_id": theirs["id"]}


@pytest.mark.parametrize("suffix", ["", "/elements", "/chunks"])
def test_cross_tenant_and_missing_document_share_one_404_shape(
    tenant_clients, suffix: str
) -> None:
    member, _, other = tenant_clients
    theirs = _upload(other, "theirs.md")

    cross = member.get(f"{DOCUMENTS_URL}/{theirs['id']}{suffix}")
    missing = member.get(f"{DOCUMENTS_URL}/no-such-document{suffix}")

    assert cross.status_code == missing.status_code == 404
    assert cross.json()["code"] == missing.json()["code"] == "NOT_FOUND"
    assert cross.json()["message"] == missing.json()["message"]


def test_same_tenant_detail_elements_and_chunks_are_readable(tenant_clients) -> None:
    member, _, _ = tenant_clients
    mine = _upload(member, "mine.md")
    chunked = member.post(f"{DOCUMENTS_URL}/{mine['id']}/chunks")
    assert chunked.status_code == 200, chunked.text

    assert member.get(f"{DOCUMENTS_URL}/{mine['id']}").status_code == 200
    assert member.get(f"{DOCUMENTS_URL}/{mine['id']}/elements").status_code == 200
    chunks = member.get(f"{DOCUMENTS_URL}/{mine['id']}/chunks")
    assert chunks.status_code == 200
    assert chunks.json()["data"]["chunks"]


# ===== 角色:本片管理员与成员都看本租户全部 =====


def test_admin_and_member_see_the_whole_tenant(tenant_clients) -> None:
    member, admin, other = tenant_clients
    members_document = _upload(member, "member.md")
    admins_document = _upload(admin, "admin.md")
    _upload(other, "other.md")

    for client in (member, admin):
        data = client.get(DOCUMENTS_URL).json()["data"]
        assert data["total"] == 2
        assert {item["id"] for item in data["items"]} == {
            members_document["id"],
            admins_document["id"],
        }

    # 同租户内互相可读:成员看管理员上传的,管理员看成员上传的
    assert member.get(f"{DOCUMENTS_URL}/{admins_document['id']}").status_code == 200
    assert admin.get(f"{DOCUMENTS_URL}/{members_document['id']}").status_code == 200
