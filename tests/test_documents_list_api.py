"""HTTP 主接缝:知识库文档清单端点(T17)。

覆盖:默认只列现行版本、筛选维度、分页与总数、排序口径。
替身上游:全部本地进行,不触任何模型服务。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

DOCUMENTS_URL = "/api/v1/documents"


def _upload(
    client: TestClient,
    *,
    name: str,
    content: bytes = "# 手册\n\n正文内容。\n".encode(),
    mime: str = "text/markdown",
    **form: str,
) -> dict:
    """上传一份知识库文档(同步形态,登记后立即解析)。"""
    response = client.post(
        DOCUMENTS_URL, files={"file": (name, content, mime)}, data=form or None
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _list(client: TestClient, **params) -> dict:
    """查询文档清单(响应信封 → data)。"""
    response = client.get(DOCUMENTS_URL, params=params or None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "OK"
    return body["data"]


# ===== 默认口径:只列现行版本,按创建时间倒序 =====


def test_list_defaults_to_latest_versions_only(client):
    """默认只列现行版本:退位的旧版本与已删除的文档不进清单。"""
    first = _upload(client, name="handbook.md")
    second = _upload(client, name="handbook.md")  # 同标识新版本 → 旧版本退位
    assert second["id"] != first["id"]

    default_list = _list(client)
    assert default_list["total"] == 1
    assert [item["id"] for item in default_list["items"]] == [second["id"]]

    # 想看历史时:参数打开,退位的旧版本重新出现
    history = _list(client, include_history=True)
    assert history["total"] == 2
    assert {item["id"] for item in history["items"]} == {first["id"], second["id"]}


def test_list_excludes_deleted_documents_by_default(client):
    """已删除的文档不进默认清单(档案保留可查,但不是现行版本)。"""
    document = _upload(client, name="obsolete.md")
    deleted = client.delete(f"{DOCUMENTS_URL}/{document['id']}")
    assert deleted.status_code == 200

    assert _list(client)["total"] == 0
    assert _list(client, include_history=True)["total"] == 1


def test_list_sorted_by_created_at_descending(client):
    """默认按创建时间倒序(最新在前)。"""
    first = _upload(client, name="a.md")
    second = _upload(client, name="b.md")
    third = _upload(client, name="c.md")

    items = _list(client)["items"]
    assert [item["id"] for item in items] == [third["id"], second["id"], first["id"]]
    created = [item["created_at"] for item in items]
    assert created == sorted(created, reverse=True)


# ===== 筛选维度 =====


def test_list_filters_by_tenant_exactly(client):
    """租户精确匹配:异租户文档不出现。"""
    acme = _upload(client, name="acme.md", tenant="acme")
    _upload(client, name="beta.md", tenant="beta")

    data = _list(client, tenant="acme")
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [acme["id"]]

    assert _list(client, tenant="nobody")["total"] == 0


def test_list_filters_by_status_exactly(client):
    """状态精确匹配。"""
    parsed = _upload(client, name="parsed.md")
    assert parsed["status"] == "parsed"

    assert _list(client, status="parsed")["total"] == 1
    assert _list(client, status="indexed")["total"] == 0


def test_list_filters_by_keyword_case_insensitive_substring(client):
    """关键字命中文件名子串,大小写不敏感。"""
    _upload(client, name="Employee-Handbook.md")
    _upload(client, name="deployment-guide.md")

    assert _list(client, keyword="employee")["total"] == 1
    assert _list(client, keyword="EMPLOYEE")["total"] == 1
    assert _list(client, keyword="book")["total"] == 1
    assert _list(client, keyword="handbook")["total"] == 1
    assert _list(client, keyword="missing")["total"] == 0


def test_list_filters_by_department_and_doc_type(client):
    """部门与文档类型精确匹配。"""
    _upload(client, name="hr.md", department="人力部", doc_type="制度")
    _upload(client, name="it.md", department="技术部", doc_type="手册")

    assert _list(client, department="人力部")["total"] == 1
    assert _list(client, doc_type="手册")["total"] == 1
    assert _list(client, department="人力部", doc_type="手册")["total"] == 0


def test_list_combines_filters_with_and(client):
    """多个筛选条件同时给出时按"与"组合。"""
    _upload(client, name="a.md", tenant="acme", department="人力部", doc_type="制度")
    _upload(client, name="b.md", tenant="acme", department="技术部", doc_type="制度")
    _upload(client, name="c.md", tenant="beta", department="人力部", doc_type="制度")

    assert _list(client, tenant="acme", department="人力部")["total"] == 1
    assert _list(client, tenant="acme", doc_type="制度")["total"] == 2
    assert (
        _list(client, tenant="acme", department="人力部", doc_type="制度")["total"] == 1
    )


# ===== 分页与总数 =====


def test_list_paginates_and_reports_total(client):
    """分页:总数不受分页影响,offset 决定起点。"""
    ids = [_upload(client, name=f"doc-{index}.md")["id"] for index in range(5)]

    first_page = _list(client, limit=2, offset=0)
    assert first_page["total"] == 5
    assert first_page["limit"] == 2
    assert first_page["offset"] == 0
    assert len(first_page["items"]) == 2

    second_page = _list(client, limit=2, offset=2)
    assert second_page["total"] == 5
    assert len(second_page["items"]) == 2

    # 页面之间不重不漏
    seen = [item["id"] for item in first_page["items"] + second_page["items"]]
    assert len(set(seen)) == 4
    assert set(seen) <= set(ids)


def test_list_offset_beyond_total_returns_empty_with_correct_total(client):
    """分页越界:返回空清单与正确总数,不报错。"""
    _upload(client, name="only.md")

    data = _list(client, limit=10, offset=100)
    assert data["total"] == 1
    assert data["items"] == []
    assert data["offset"] == 100


def test_list_total_reflects_filters_not_page_size(client):
    """总数是符合筛选条件的总数,不受分页影响。"""
    for index in range(3):
        _upload(client, name=f"doc-{index}.md", tenant="acme")
    _upload(client, name="other.md", tenant="beta")

    data = _list(client, tenant="acme", limit=1)
    assert data["total"] == 3
    assert len(data["items"]) == 1


# ===== 响应契约 =====


def test_list_response_uses_envelope_with_trace_id(client):
    """响应走统一响应信封,带 trace_id。"""
    response = client.get(DOCUMENTS_URL)
    body = response.json()

    assert body["code"] == "OK"
    assert body["details"] is None
    assert body["trace_id"]
    assert body["timestamp"]
    assert set(body["data"]) == {"total", "limit", "offset", "items"}


def test_list_items_expose_document_view_fields(client):
    """清单条目与单份查询的档案视图同形。"""
    document = _upload(client, name="view.md", tenant="acme", department="人力部")

    item = _list(client)["items"][0]
    assert item["id"] == document["id"]
    assert item["file_name"] == "view.md"
    assert item["tenant"] == "acme"
    assert item["department"] == "人力部"
    assert item["status"] == "parsed"
    assert item["is_latest"] is True
