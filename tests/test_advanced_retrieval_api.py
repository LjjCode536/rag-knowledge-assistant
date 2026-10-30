"""HTTP 主接缝:M4 高级检索开关组合走真实路由(不触网)。

验收口径:
- 开关组合经 create_app 配置生效;/search /chat 契约形状不变(ADR-0004);
- 纯关键词通道(向量权重 0)能命中的前提是入库时关键词索引已写入;
- 重排走主接缝:注入替身排序反映到最终响应。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from tests.test_embeddings_api import ALPHA_TEXT, _chunk, _embed, _upload
from tests.test_retrieval_pipeline import FakeReranker


def _index_alpha(client: TestClient) -> dict:
    document_id = _upload(
        client,
        "hr.txt",
        ALPHA_TEXT,
        tenant="acme",
        department="人力部",
        doc_type="政策",
        version="v2",
    )["data"]["id"]
    _chunk(client, document_id)
    _embed(client, document_id)
    return {"document_id": document_id}


@pytest.fixture
def hybrid_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "use_hybrid": True,
            "retrieval_vector_weight": 0.0,
            "retrieval_keyword_weight": 1.0,
        }
    )


@pytest.fixture
def hybrid_client(hybrid_settings: Settings):
    app = create_app(hybrid_settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def rerank_client(settings: Settings):
    settings = settings.model_copy(update={"use_rerank": True})
    app = create_app(
        settings, embedder=DeterministicEmbedder(), reranker=FakeReranker()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_pure_keyword_channel_hits_through_http_main_seam(hybrid_client) -> None:
    indexed = _index_alpha(hybrid_client)

    response = hybrid_client.post("/api/v1/search", json={"query": "休假 工资", "top_k": 3})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"] > 0
    hits = data["hits"]
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(
        hit["chunk"]["document_id"] == indexed["document_id"] for hit in hits
    )


def test_rerank_reorders_search_hits_via_main_seam(rerank_client) -> None:
    indexed = _index_alpha(rerank_client)

    data = rerank_client.post(
        "/api/v1/search", json={"query": "休假 工资 运维", "top_k": 3}
    ).json()["data"]

    hits = data["hits"]
    assert hits, "重排开时仍应有命中"
    # FakeReranker 按内容长度降序、分数按位置衰减;主缝只断言形状与排序
    assert [hit["score"] for hit in hits] == sorted(
        [hit["score"] for hit in hits], reverse=True
    )
    assert hits[0]["score"] <= 1.0
    for hit in hits:
        assert hit["chunk"]["document_id"] == indexed["document_id"]


def test_chat_main_seam_unchanged_with_advanced_pipeline(rerank_client) -> None:
    """ADR-0004:M4 只替换检索内部,问答编排与响应字段不变。"""
    _index_alpha(rerank_client)

    response = rerank_client.post(
        "/api/v1/chat", json={"question": "休假怎么", "top_k": 3}
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["refused"] is False
    assert data["citations"]
    for field in ("query_id", "question", "answer", "top_k", "min_score", "latency_ms"):
        assert field in data


def test_search_flag_off_keeps_legacy_scores(settings: Settings) -> None:
    """显式全关 = T06 时序;与新默认组合无关的口径断言。"""
    settings = settings.model_copy(
        update={
            "use_hybrid": False,
            "use_mmr": False,
            "use_rerank": False,
            "use_query_rewrite": False,
            "use_multi_query": False,
        }
    )
    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        _index_alpha(client)

        data = client.post(
            "/api/v1/search", json={"query": "休假 工资 运维", "top_k": 3}
        ).json()["data"]

    assert data["hits"][0]["score"] == pytest.approx(0.816497, abs=1e-5)
