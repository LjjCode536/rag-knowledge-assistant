"""HTTP 主接缝:相似度检索(T04 / M3 基线上半)。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.fake_embedder import DeterministicEmbedder
from tests.test_embeddings_api import (
    ALPHA_TEXT,
    BETA_TEXT,
    _chunk,
    _embed,
    _upload,
)

SEARCH_URL = "/api/v1/search"


@pytest.fixture
def indexed_client(settings):
    """注入假向量替身,并预置 acme / beta 两个已入库文档。"""
    from app.main import create_app

    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        alpha = _upload(
            client,
            "hr.txt",
            ALPHA_TEXT,
            tenant="acme",
            department="人力部",
            doc_type="政策",
            version="v2",
        )["data"]["id"]
        alpha_chunks = _chunk(client, alpha)
        _embed(client, alpha)

        beta = _upload(
            client,
            "ops.txt",
            BETA_TEXT,
            tenant="beta",
            department="运维部",
            doc_type="手册",
        )["data"]["id"]
        beta_chunks = _chunk(client, beta)
        _embed(client, beta)
        yield client, {
            "alpha_id": alpha,
            "alpha_chunks": alpha_chunks,
            "beta_id": beta,
            "beta_chunks": beta_chunks,
        }


def _search(client: TestClient, **body: object) -> dict:
    response = client.post(SEARCH_URL, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["trace_id"] == response.headers["X-Request-ID"]
    return response.json()["data"]


def test_search_without_key_returns_unified_upstream_error(client):
    response = client.post(SEARCH_URL, json={"query": "休假 工资"})

    assert response.status_code == 503
    assert response.json()["code"] == "UPSTREAM_NOT_CONFIGURED"
    assert response.json()["details"]["provider"] == "siliconflow"


def test_search_returns_scored_chunks_with_full_metadata(indexed_client):
    client, indexed = indexed_client

    data = _search(client, query="休假 工资 运维", top_k=5)

    assert data["query"] == "休假 工资 运维"
    assert data["top_k"] == 5
    assert data["total"] == 4  # acme 3 个切片 + beta 1 个切片
    hits = data["hits"]
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)  # 同题查询按分数排序
    # 与查询词面完全一致的切片("休假 工资")排名第一
    assert hits[0]["chunk"]["chunk_id"] == indexed["alpha_chunks"][0]["chunk_id"]
    assert hits[0]["score"] == pytest.approx(0.816497, abs=1e-5)

    hit = hits[0]
    chunk = hit["chunk"]
    assert chunk["document_id"] == indexed["alpha_id"]
    assert chunk["tenant"] == "acme"
    assert chunk["department"] == "人力部"
    assert chunk["doc_type"] == "政策"
    assert chunk["version"] == "v2"
    for field in ("chunk_id", "index", "content", "page_number", "title"):
        assert field in chunk
    beta_hit = next(
        h for h in hits if h["chunk"]["chunk_id"] == indexed["beta_chunks"][0]["chunk_id"]
    )
    assert beta_hit["chunk"]["tenant"] == "beta"


def test_search_tenant_filter_is_pre_retrieval_permission(indexed_client):
    """权限维度在检索前过滤:beta 切片不得占用 top-k 名额。"""
    client, indexed = indexed_client

    data = _search(client, query="休假 工资 运维", top_k=3, filters={"tenant": "acme"})

    assert data["filters"] == {"tenant": "acme"}
    assert data["total"] == 3
    hits = data["hits"]
    assert all(hit["chunk"]["tenant"] == "acme" for hit in hits)
    assert all(
        hit["chunk"]["chunk_id"] != indexed["beta_chunks"][0]["chunk_id"] for hit in hits
    )
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)


def test_search_filters_combine_across_metadata_fields(indexed_client):
    client, indexed = indexed_client

    data = _search(
        client,
        query="休假 工资 运维",
        filters={"department": "运维部", "doc_type": "手册"},
    )
    assert data["total"] == 1
    assert data["hits"][0]["chunk"]["document_id"] == indexed["beta_id"]

    data = _search(
        client,
        query="休假 工资 运维",
        filters={"tenant": "acme", "version": "v2", "document_id": indexed["alpha_id"]},
    )
    assert data["total"] == 3
    assert all(hit["chunk"]["version"] == "v2" for hit in data["hits"])

    data = _search(client, query="休假 工资 运维", filters={"department": "不存在"})
    assert data["total"] == 0
    assert data["hits"] == []


def test_search_empty_index_returns_zero_hits(settings, upload_dir):
    from app.main import create_app

    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app) as client:
        data = _search(client, query="垃圾分类怎么处理")
    assert data["total"] == 0
    assert data["hits"] == []


def test_search_rejects_blank_query_and_zero_top_k(indexed_client):
    client, _ = indexed_client

    response = client.post(SEARCH_URL, json={"query": ""})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

    response = client.post(SEARCH_URL, json={"query": "休假", "top_k": 0})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
