"""HTTP 主接缝:相似度检索(T04 / M3 基线上半)。

授权范围由 token 主体构造:检索只返回本租户切片;收窄条件不得越界。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.integrations.llm.deterministic import DeterministicEmbedder
from tests.support import OTHER_TENANT, authorized_client
from tests.test_embeddings_api import (
    ALPHA_TEXT,
    BETA_TEXT,
    _chunk,
    _embed,
    _upload,
)

SEARCH_URL = "/api/v1/search"


def _seed(
    app,
) -> Iterator[tuple[TestClient, TestClient, dict]]:
    """注入假向量替身,默认租户与另一租户各一份已入库文档。

    切片打分断言固定 T04 合同口径:显式全关,不随应用默认组合漂移。
    """
    with authorized_client(app, raise_server_exceptions=False) as client, authorized_client(
        app, OTHER_TENANT.principal, raise_server_exceptions=False
    ) as other_client:
        alpha = _upload(
            client,
            "hr.txt",
            ALPHA_TEXT,
            department="研发部",
            doc_type="政策",
            version="v2",
        )["data"]["id"]
        alpha_chunks = _chunk(client, alpha)
        _embed(client, alpha)

        beta = _upload(
            other_client,
            "ops.txt",
            BETA_TEXT,
            department="市场部",
            doc_type="手册",
        )["data"]["id"]
        beta_chunks = _chunk(other_client, beta)
        _embed(other_client, beta)
        yield client, other_client, {
            "alpha_id": alpha,
            "alpha_chunks": alpha_chunks,
            "beta_id": beta,
            "beta_chunks": beta_chunks,
        }


@pytest.fixture
def indexed_client(settings) -> Iterator[tuple[TestClient, TestClient, dict]]:
    from app.main import create_app

    settings = settings.model_copy(
        update={
            "use_query_rewrite": False,
            "use_multi_query": False,
            "use_hybrid": False,
            "use_mmr": False,
            "use_rerank": False,
        }
    )
    app = create_app(settings, embedder=DeterministicEmbedder())
    yield from _seed(app)


def _search(client: TestClient, **body: object) -> dict:
    response = client.post(SEARCH_URL, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["trace_id"] == response.headers["X-Request-ID"]
    return response.json()["data"]


# ===== 向量库后端切换(T12):同题同结果,只换配置 =====


@pytest.fixture
def chroma_indexed_client(settings, tmp_path) -> Iterator[tuple[TestClient, TestClient, dict]]:
    """与 indexed_client 同数据同口径,仅向量库经配置切到 chroma(内存 → 第二层)。

    切片打分断言与内存基线一致:显式全关,不随应用默认组合漂移。
    """
    from app.main import create_app

    chroma_settings = settings.model_copy(
        update={
            "vector_store": "chroma",
            "chroma_path": tmp_path / "chroma",
            "use_query_rewrite": False,
            "use_multi_query": False,
            "use_hybrid": False,
            "use_mmr": False,
            "use_rerank": False,
        }
    )
    app = create_app(chroma_settings, embedder=DeterministicEmbedder())
    try:
        yield from _seed(app)
    finally:
        app.state.vector_store.close()


def test_chroma_backend_same_results_as_memory_baseline(
    chroma_indexed_client, indexed_client
):
    """同数据同查询:切换前后主缝结果一致(同序同 chunk、分数仅 float32 微差)。

    chunk_id 每次切片重新生成,不能逐位对比;主缝一致性以「文档#index 排序」
    与分数为准(评估报告的定位口径)。
    """
    chroma_client, _, chroma_seeds = chroma_indexed_client
    memory_client, _, memory_seeds = indexed_client

    def locator(hits, seeds):
        doc_label = {seeds["alpha_id"]: "alpha", seeds["beta_id"]: "beta"}
        return [(doc_label[h["chunk"]["document_id"]], h["chunk"]["index"]) for h in hits]

    body = {"query": "休假 工资 运维", "top_k": 5}
    memory_data = _search(memory_client, **body)
    chroma_data = _search(chroma_client, **body)

    assert locator(chroma_data["hits"], chroma_seeds) == locator(memory_data["hits"], memory_seeds)
    for c, m in zip(chroma_data["hits"], memory_data["hits"], strict=True):
        assert c["score"] == pytest.approx(m["score"], abs=1e-5)
    # 默认租户只有 alpha 的 3 个切片;beta 租户的切片不进入候选
    assert chroma_data["total"] == memory_data["total"] == 3


def test_chroma_backend_metadata_filter_is_pre_retrieval_permission(
    chroma_indexed_client,
):
    """收窄条件在 Chroma 上等价生效:被过滤切片不占 top-k 名额。"""
    chroma_client, _, indexed = chroma_indexed_client

    data = _search(
        chroma_client,
        query="休假 工资 运维",
        top_k=3,
        filters={"department": "研发部"},
    )

    assert data["filters"] == {"tenant": "default", "department": "研发部"}
    assert data["total"] == 3
    assert all(hit["chunk"]["tenant"] == "default" for hit in data["hits"])
    assert all(hit["chunk"]["document_id"] == indexed["alpha_id"] for hit in data["hits"])
    scores = [hit["score"] for hit in data["hits"]]
    assert scores == sorted(scores, reverse=True)


def test_search_without_key_returns_unified_upstream_error(client):
    response = client.post(SEARCH_URL, json={"query": "休假 工资"})

    assert response.status_code == 503
    assert response.json()["code"] == "UPSTREAM_NOT_CONFIGURED"
    assert response.json()["details"]["provider"] == "siliconflow"


def test_search_returns_scored_chunks_with_full_metadata(indexed_client):
    client, other_client, indexed = indexed_client

    data = _search(client, query="休假 工资 运维", top_k=5)

    assert data["query"] == "休假 工资 运维"
    assert data["top_k"] == 5
    assert data["total"] == 3  # 只有默认租户 alpha 的 3 个切片
    hits = data["hits"]
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)  # 同题查询按分数排序
    # 与查询词面完全一致的切片("休假 工资")排名第一
    assert hits[0]["chunk"]["chunk_id"] == indexed["alpha_chunks"][0]["chunk_id"]
    assert hits[0]["score"] == pytest.approx(0.816497, abs=1e-5)

    hit = hits[0]
    chunk = hit["chunk"]
    assert chunk["document_id"] == indexed["alpha_id"]
    assert chunk["tenant"] == "default"
    assert chunk["department"] == "研发部"
    assert chunk["doc_type"] == "政策"
    assert chunk["version"] == "v2"
    for field in ("chunk_id", "index", "content", "page_number", "title"):
        assert field in chunk

    # 另一租户的主体只能看到自己的切片(完整元数据照常返回)
    other_data = _search(other_client, query="休假 工资 运维", top_k=5)
    assert other_data["filters"] == {"tenant": "other"}
    assert other_data["total"] == 1
    beta_hit = other_data["hits"][0]
    assert beta_hit["chunk"]["chunk_id"] == indexed["beta_chunks"][0]["chunk_id"]
    assert beta_hit["chunk"]["tenant"] == "other"


def test_search_scope_is_server_side_and_rejects_tenant_narrowing(indexed_client):
    client, other_client, indexed = indexed_client

    response = client.post(
        SEARCH_URL, json={"query": "休假 工资 运维", "filters": {"tenant": "other"}}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

    data = _search(client, query="休假 工资 运维", top_k=3)
    assert data["filters"] == {"tenant": "default"}
    assert all(hit["chunk"]["tenant"] == "default" for hit in data["hits"])
    assert all(
        hit["chunk"]["chunk_id"] != indexed["beta_chunks"][0]["chunk_id"]
        for hit in data["hits"]
    )
    scores = [hit["score"] for hit in data["hits"]]
    assert scores == sorted(scores, reverse=True)

    other_data = _search(other_client, query="休假 工资 运维", top_k=3)
    assert other_data["filters"] == {"tenant": "other"}
    assert all(hit["chunk"]["tenant"] == "other" for hit in other_data["hits"])


def test_search_filters_combine_across_metadata_fields(indexed_client):
    client, other_client, indexed = indexed_client

    data = _search(
        other_client,
        query="休假 工资 运维",
        filters={"department": "市场部", "doc_type": "手册"},
    )
    assert data["total"] == 1
    assert data["hits"][0]["chunk"]["document_id"] == indexed["beta_id"]

    data = _search(
        client,
        query="休假 工资 运维",
        filters={"version": "v2", "document_id": indexed["alpha_id"]},
    )
    assert data["total"] == 3
    assert data["filters"] == {
        "tenant": "default",
        "version": "v2",
        "document_id": indexed["alpha_id"],
    }
    assert all(hit["chunk"]["version"] == "v2" for hit in data["hits"])

    data = _search(client, query="休假 工资 运维", filters={"department": "不存在"})
    assert data["total"] == 0
    assert data["hits"] == []


def test_search_empty_index_returns_zero_hits(settings, upload_dir):
    from app.main import create_app

    app = create_app(settings, embedder=DeterministicEmbedder())
    with authorized_client(app) as client:
        data = _search(client, query="垃圾分类怎么处理")
    assert data["total"] == 0
    assert data["hits"] == []


def test_search_rejects_blank_query_and_zero_top_k(indexed_client):
    client, _, _ = indexed_client

    response = client.post(SEARCH_URL, json={"query": ""})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

    response = client.post(SEARCH_URL, json={"query": "休假", "top_k": 0})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
