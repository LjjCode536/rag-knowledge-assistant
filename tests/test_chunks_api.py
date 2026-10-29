"""HTTP 主接缝:切片链路(T03 / M2)。

覆盖切片元数据齐全且可溯源、策略与参数即时生效、表格原子性与质量告警;
替身上游:切分全程本地进行,不触任何模型服务。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

DOCUMENTS_URL = "/api/v1/documents"

TABLE_CONTENT = "参数 | 值\n超时 | 30\n并发 | 5"

# 标题 + 520 字散文段 + 表格 + 又一段 520 字散文:默认策略下应切出多个切片
MARKDOWN = (
    "# 配置指南\n"
    "\n"
    f"{'A' * 520}\n"
    "\n"
    f"| 参数 | 值 |\n"
    f"| --- | --- |\n"
    f"| 超时 | 30 |\n"
    f"| 并发 | 5 |\n"
    "\n"
    f"{'B' * 520}\n"
)


def _upload(client: TestClient, *, name: str, content: bytes, mime: str, **form: str) -> dict:
    response = client.post(DOCUMENTS_URL, files={"file": (name, content, mime)}, data=form)
    return response.json()


# ===== 切片元数据齐全、可溯源,默认策略切出切片 =====


def test_chunk_with_default_strategy_carries_complete_metadata(client):
    body = _upload(
        client,
        name="guide.md",
        content=MARKDOWN.encode(),
        mime="text/markdown",
        tenant="acme",
        department="研发部",
        doc_type="运维手册",
        version="v2",
    )
    document_id = body["data"]["id"]
    assert (body["data"]["tenant"], body["data"]["version"]) == ("acme", "v2")

    response = client.post(f"{DOCUMENTS_URL}/{document_id}/chunks")
    assert response.status_code == 200
    run = response.json()
    assert run["code"] == "OK"
    assert run["trace_id"] == response.headers["X-Request-ID"]

    data = run["data"]
    assert data["strategy"] == "table_aware"  # 默认策略与参数来自统一配置
    assert data["chunk_size"] == 500
    assert data["chunk_overlap"] == 50
    assert data["document"]["status"] == "chunked"

    chunks = data["chunks"]
    assert len(chunks) >= 3  # 两段 520 字散文可分 + 一张原子表格
    assert [chunk["index"] for chunk in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk["document_id"] == document_id
        assert chunk["chunk_id"]
        for field in ("tenant", "department", "doc_type", "version", "page_number", "title"):
            assert field in chunk
        assert chunk["tenant"] == "acme"
        assert chunk["department"] == "研发部"
        assert chunk["doc_type"] == "运维手册"
        assert chunk["version"] == "v2"
        assert len(chunk["content"]) <= 500  # 尺寸参数即时生效
        assert 0 <= chunk["element_index_start"] <= chunk["element_index_end"] <= 3  # 可溯源

    table_chunks = [chunk for chunk in chunks if TABLE_CONTENT in chunk["content"]]
    assert len(table_chunks) == 1  # 表格不被无意义切断
    assert all(chunk["title"] in (None, "配置指南") for chunk in chunks)

    view = client.get(f"{DOCUMENTS_URL}/{document_id}").json()["data"]
    assert view["status"] == "chunked"
    assert view["chunk_count"] == len(chunks)

    listed = client.get(f"{DOCUMENTS_URL}/{document_id}/chunks").json()["data"]
    assert listed["status"] == "chunked"
    assert listed["chunk_count"] == len(chunks)
    assert [chunk["chunk_id"] for chunk in listed["chunks"]] == [
        chunk["chunk_id"] for chunk in chunks
    ]
    assert [chunk["index"] for chunk in listed["chunks"]] == [
        chunk["index"] for chunk in chunks
    ]
    assert listed["warnings"] == []


# ===== 重切:策略与参数即时生效 =====


def test_rechunk_switches_strategy_and_params_immediately(client):
    body = _upload(client, name="guide.md", content=MARKDOWN.encode(), mime="text/markdown")
    document_id = body["data"]["id"]
    # 未提供元数据时的默认值:租户 default、版本 v1
    assert (body["data"]["tenant"], body["data"]["version"]) == ("default", "v1")
    first = client.post(f"{DOCUMENTS_URL}/{document_id}/chunks").json()["data"]
    first_ids = {chunk["chunk_id"] for chunk in first["chunks"]}

    rerun = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks",
        json={"strategy": "paragraph", "chunk_size": 200, "chunk_overlap": 40},
    )
    assert rerun.status_code == 200
    data = rerun.json()["data"]
    assert data["strategy"] == "paragraph"
    assert data["chunk_size"] == 200
    assert data["chunk_overlap"] == 40
    assert all(len(chunk["content"]) <= 200 for chunk in data["chunks"])  # 参数即时生效
    assert len(data["chunks"]) > len(first["chunks"])  # 更小尺寸切出更多切片

    listed = client.get(f"{DOCUMENTS_URL}/{document_id}/chunks").json()["data"]
    new_ids = {chunk["chunk_id"] for chunk in listed["chunks"]}
    assert len(listed["chunks"]) == data["chunk_count"]
    assert all(len(chunk["content"]) <= 200 for chunk in listed["chunks"])
    assert new_ids != first_ids  # 重切整组替换,旧切片作废

    view = client.get(f"{DOCUMENTS_URL}/{document_id}").json()["data"]
    assert view["status"] == "chunked"
    assert view["chunk_count"] == data["chunk_count"]


# ===== 标题策略:按标题分节,节标题进切片元数据 =====


def test_heading_strategy_splits_by_sections_keeping_titles(client):
    markdown = (
        "# 总则\n"
        "\n"
        "一般约定如下。\n"
        "\n"
        "- 条款甲\n"
        "- 条款乙\n"
        "\n"
        "# 退费政策\n"
        "\n"
        "7 天内可退。\n"
        "\n"
        "# 联系方式\n"
        "\n"
        "工作日 10:00-18:00。\n"
    )
    body = _upload(client, name="handbook.md", content=markdown.encode(), mime="text/markdown")
    document_id = body["data"]["id"]

    response = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks", json={"strategy": "heading"}
    )
    chunks = response.json()["data"]["chunks"]

    assert [chunk["title"] for chunk in chunks] == ["总则", "退费政策", "联系方式"]
    assert "一般约定如下。" in chunks[0]["content"] and "条款甲" in chunks[0]["content"]
    assert "7 天内可退。" in chunks[1]["content"]
    assert not any("总则" in chunk["content"] for chunk in chunks)  # 标题文本不重复进内容
    assert [(c["element_index_start"], c["element_index_end"]) for c in chunks] == [
        (1, 3),  # 总则一节覆盖段落与两条列表项
        (5, 5),
        (7, 7),
    ]


# ===== 元素策略:一个解析元素一个切片 =====


def test_element_strategy_makes_one_chunk_per_element(client):
    body = _upload(client, name="guide.md", content=MARKDOWN.encode(), mime="text/markdown")
    document_id = body["data"]["id"]

    response = client.post(f"{DOCUMENTS_URL}/{document_id}/chunks", json={"strategy": "element"})
    data = response.json()["data"]
    chunks = data["chunks"]

    assert data["strategy"] == "element"
    assert data["chunk_count"] == 4  # 标题 / 段落 / 表格 / 段落 各一片
    texts = [chunk["content"] for chunk in chunks]
    assert texts[0] == "配置指南"  # 标题元素自身内容保留
    assert texts[1] == "A" * 520 and texts[3] == "B" * 520
    assert texts[2] == TABLE_CONTENT  # 结构永不切断
    assert [(c["element_index_start"], c["element_index_end"]) for c in chunks] == [
        (0, 0),
        (1, 1),
        (2, 2),
        (3, 3),
    ]


# ===== 表格感知:表格 / 代码原子不可切,与段落基线对比 =====

# 18 行数据:整表内容必然超过 chunk_size=120,基线策略被迫切表
_PRICE_ROWS = [
    ("App", 3), ("Web", 5), ("热线", 1), ("邮寄", 7), ("上门", 2), ("全站", 9),
    ("站点", 8), ("分部", 6), ("大区", 4), ("门店", 5), ("专柜", 2), ("网点", 4),
    ("代理", 6), ("直营", 8), ("联营", 3), ("外包", 2), ("总部", 1), ("值守", 5),
]
FULL_TABLE = "渠道 | 时效\n" + "\n".join(f"{name} | {days} 天" for name, days in _PRICE_ROWS)
TABLE_ROWS_MARKDOWN = (
    "# 价格表\n"
    "\n"
    f"{'P' * 150}\n"
    "\n"
    "| 渠道 | 时效 |\n"
    "| --- | --- |\n"
    + "".join(f"| {name} | {days} 天 |\n" for name, days in _PRICE_ROWS)
    + "\n"
    f"{'Q' * 150}\n"
)


def test_table_aware_keeps_table_whole_while_paragraph_cuts_it(client):
    body = _upload(
        client, name="price.md", content=TABLE_ROWS_MARKDOWN.encode(), mime="text/markdown"
    )
    document_id = body["data"]["id"]

    aware = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks",
        json={"strategy": "table_aware", "chunk_size": 120, "chunk_overlap": 20},
    ).json()["data"]
    aware_table = [chunk for chunk in aware["chunks"] if "渠道" in chunk["content"]]
    assert len(aware_table) == 1  # 表格只出现在一个切片里
    assert aware_table[0]["content"] == FULL_TABLE  # 整表原子出现,未被切断

    cut = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks",
        json={"strategy": "paragraph", "chunk_size": 120, "chunk_overlap": 20},
    ).json()["data"]
    partial = [
        chunk
        for chunk in cut["chunks"]
        if "渠道" in chunk["content"] and chunk["content"] != FULL_TABLE
    ]
    assert partial  # 基线策略无结构感知:表格被切断 / 拆散


def test_code_blocks_stay_whole_under_table_aware_strategy(client):
    code_lines = "\n".join(f"line_{number} = {number}" for number in range(40))  # 约 500 字
    markdown = f"# 脚本\n\n```python\n{code_lines}\n```\n"
    body = _upload(client, name="script.md", content=markdown.encode(), mime="text/markdown")
    document_id = body["data"]["id"]

    run = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks",
        json={"strategy": "table_aware", "chunk_size": 100, "chunk_overlap": 20},
    ).json()["data"]

    code_chunks = [chunk for chunk in run["chunks"] if "line_1 = 1" in chunk["content"]]
    assert len(code_chunks) == 1  # 代码块只出现在一个切片里
    assert code_chunks[0]["content"] == code_lines  # 整块代码原子出现,未被切断
    assert code_chunks[0]["title"] == "脚本"


# ===== 切片质量告警:过短 / 超长可见,且贯穿 POST 与 GET =====


def test_too_long_warning_visible_for_oversized_chunks(client):
    body = _upload(client, name="guide.md", content=MARKDOWN.encode(), mime="text/markdown")
    document_id = body["data"]["id"]

    run = client.post(f"{DOCUMENTS_URL}/{document_id}/chunks", json={"strategy": "element"}).json()
    warnings = run["data"]["warnings"]
    assert {w["code"] for w in warnings} == {"CHUNK_TOO_LONG", "CHUNK_TOO_SHORT"}
    too_long = next(w for w in warnings if w["code"] == "CHUNK_TOO_LONG")
    assert too_long["details"]["count"] == 2  # 两段 520 字元素都超过 chunk_size 500
    assert too_long["message"]

    listed = client.get(f"{DOCUMENTS_URL}/{document_id}/chunks").json()["data"]
    assert {w["code"] for w in listed["warnings"]} == {"CHUNK_TOO_LONG", "CHUNK_TOO_SHORT"}


def test_too_short_warning_visible_with_threshold_from_config(client):
    body = _upload(client, name="notes.txt", content="短句一\n\n短句二".encode(), mime="text/plain")
    document_id = body["data"]["id"]

    run = client.post(f"{DOCUMENTS_URL}/{document_id}/chunks").json()
    data = run["data"]
    assert data["chunk_count"] == 1  # 连续散文合并成一片
    assert len(data["chunks"][0]["content"]) < 20
    assert [w["code"] for w in data["warnings"]] == ["CHUNK_TOO_SHORT"]
    assert data["warnings"][0]["details"]["threshold"] == 20

    listed = client.get(f"{DOCUMENTS_URL}/{document_id}/chunks").json()["data"]
    assert [w["code"] for w in listed["warnings"]] == ["CHUNK_TOO_SHORT"]


# ===== 错误路径:稳定错误码 + 统一信封 =====


def test_chunk_unknown_document_returns_404_envelope(client):
    response = client.post(f"{DOCUMENTS_URL}/no-such-document/chunks")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["details"] == {"document_id": "no-such-document"}
    assert body["trace_id"] == response.headers["X-Request-ID"]


def test_chunk_failed_document_returns_409_with_state_detail(client):
    body = _upload(client, name="broken.pdf", content=b"this is not a pdf", mime="application/pdf")
    document_id = body["data"]["id"]
    assert body["data"]["status"] == "failed"

    response = client.post(f"{DOCUMENTS_URL}/{document_id}/chunks")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "INVALID_DOCUMENT_STATE"
    assert body["details"]["status"] == "failed"


def test_chunk_param_validation_rejects_overlap_larger_than_size(client):
    body = _upload(client, name="notes.txt", content="常规段落。".encode(), mime="text/plain")
    document_id = body["data"]["id"]

    response = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks",
        json={"chunk_size": 10, "chunk_overlap": 10},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_chunk_param_validation_rejects_unknown_strategy(client):
    body = _upload(client, name="notes.txt", content="常规段落。".encode(), mime="text/plain")
    document_id = body["data"]["id"]

    response = client.post(f"{DOCUMENTS_URL}/{document_id}/chunks", json={"strategy": "bogus"})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
