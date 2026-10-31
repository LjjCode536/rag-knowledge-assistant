"""HTTP 主接缝:运营统计与成本统计端点(T16)。

替身上游:沿用确定性假向量替身,问答全程不触网;
覆盖 /stats 的核心指标(文档数 / 问答量 / 拒答率 / 平均延迟)、成本明细的
时间段查询、分桶序列与参数校验。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app

DOCUMENTS_URL = "/api/v1/documents"
CHAT_URL = "/api/v1/chat"
STATS_URL = "/api/v1/stats"

HANDBOOK = "# 休假制度\n\n员工入职满一年后,可以申请年假。\n"

LEAVE_QUESTION = "入职多久可以申请年假?"
UNRELATED_QUESTION = "垃圾分类怎么处理?"


def _index(client: TestClient, name: str, content: str, **form: str) -> dict:
    """上传 → 按标题切片 → 向量化入库,返回文档 id 与切片。"""
    response = client.post(
        DOCUMENTS_URL,
        files={"file": (name, content.encode(), "text/plain")},
        data=form,
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["data"]["id"]

    response = client.post(
        f"{DOCUMENTS_URL}/{document_id}/chunks", json={"strategy": "heading"}
    )
    assert response.status_code == 200, response.text
    chunks = response.json()["data"]["chunks"]

    response = client.post(f"{DOCUMENTS_URL}/{document_id}/embeddings")
    assert response.status_code == 200, response.text
    return {"document_id": document_id, "chunks": chunks}


def _chat(client: TestClient, question: str) -> dict:
    response = client.post(CHAT_URL, json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.fixture
def indexed_client(settings, upload_dir) -> Iterator[TestClient]:
    """注入假向量替身,预置一份已入库的员工手册。"""
    app = create_app(settings, embedder=DeterministicEmbedder())
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client, "employee.md", HANDBOOK, tenant="acme")
        yield client


def test_stats_on_empty_system_reports_zero_scale(indexed_client):
    """还没有问答时:规模有数,比率与延迟为 None(没有样本不等于 0)。"""
    response = indexed_client.get(STATS_URL)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "OK"
    assert body["trace_id"] == response.headers["X-Request-ID"]
    data = body["data"]

    assert data["document_count"] == 1
    assert data["indexed_document_count"] == 1
    assert data["chunk_count"] == 1
    assert data["qa"]["query_count"] == 0
    assert data["qa"]["refused_count"] == 0
    assert data["qa"]["refusal_rate"] is None
    assert data["qa"]["avg_latency_ms"] is None
    assert data["cost"]["total_cost"] == 0.0
    assert data["buckets"] == []


def test_stats_reports_query_volume_and_refusal_rate(indexed_client):
    """核心指标:问答量、拒答数与拒答率。"""
    client = indexed_client
    _chat(client, LEAVE_QUESTION)
    _chat(client, LEAVE_QUESTION)
    refused = _chat(client, UNRELATED_QUESTION)

    assert refused["refused"] is True

    data = client.get(STATS_URL).json()["data"]

    assert data["qa"]["query_count"] == 3
    assert data["qa"]["refused_count"] == 1
    assert data["qa"]["refusal_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert data["qa"]["avg_latency_ms"] > 0


def test_stats_buckets_are_returned_ascending(indexed_client):
    """分桶序列按桶起点升序。"""
    client = indexed_client
    _chat(client, LEAVE_QUESTION)

    data = client.get(STATS_URL, params={"bucket": "hour"}).json()["data"]

    assert data["bucket_kind"] == "hour"
    assert len(data["buckets"]) == 1
    bucket = data["buckets"][0]
    assert bucket["qa"]["query_count"] == 1
    start = datetime.fromisoformat(bucket["bucket_start"])
    end = datetime.fromisoformat(bucket["bucket_end"])
    assert (end - start) == timedelta(hours=1)


def test_cost_endpoint_reports_token_usage_and_estimate(indexed_client):
    """成本端点:Token 用量 + 估算成本(规则版生成器零 Token,成本为零)。"""
    client = indexed_client
    _chat(client, LEAVE_QUESTION)

    response = client.get(f"{STATS_URL}/cost")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["token_usage"]["total_tokens"] == 0
    assert data["cost"]["total_cost"] == 0.0
    assert data["cost"]["currency"] == "CNY"


def test_cost_endpoint_prices_tokens_with_configured_rates(settings, upload_dir):
    """成本口径:按配置单价把 Token 用量折算成金额。

    用注入的生成器替身产生确定的 Token 用量,验证折算公式与配置一致。
    """
    from app.domain.qa import GenerationUsage, GeneratorKind
    from app.services.generation import GeneratedAnswer

    class _UsageGenerator:
        kind = GeneratorKind.LLM

        async def generate(self, question, *, context):
            return GeneratedAnswer(
                text="员工入职满一年后,可以申请年假。",
                usage=GenerationUsage(
                    prompt_tokens=2000, completion_tokens=1000, total_tokens=3000
                ),
            )

    configured = settings.model_copy(
        update={
            "cost_prompt_price_per_1k": 0.002,
            "cost_completion_price_per_1k": 0.006,
        }
    )
    app = create_app(
        configured,
        embedder=DeterministicEmbedder(),
        answer_generator=_UsageGenerator(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client, "employee.md", HANDBOOK, tenant="acme")
        _chat(client, LEAVE_QUESTION)

        data = client.get(f"{STATS_URL}/cost").json()["data"]

    assert data["token_usage"]["prompt_tokens"] == 2000
    assert data["token_usage"]["completion_tokens"] == 1000
    assert data["token_usage"]["total_tokens"] == 3000
    # prompt 2000 x 0.002/千 = 0.004;completion 1000 x 0.006/千 = 0.006
    assert data["cost"]["prompt_cost"] == pytest.approx(0.004)
    assert data["cost"]["completion_cost"] == pytest.approx(0.006)
    assert data["cost"]["total_cost"] == pytest.approx(0.01)


def test_stats_accepts_time_range_parameters(indexed_client):
    """时间段参数:只统计区间内的问答。"""
    client = indexed_client
    _chat(client, LEAVE_QUESTION)

    now = datetime.now(UTC)
    response = client.get(
        STATS_URL,
        params={
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["qa"]["query_count"] == 1
    assert data["start"] is not None
    assert data["end"] is not None


def test_stats_rejects_inverted_time_range(indexed_client):
    """起点晚于终点:400 BAD_REQUEST,不落到 500。"""
    now = datetime.now(UTC)
    response = indexed_client.get(
        STATS_URL,
        params={
            "start": (now + timedelta(hours=1)).isoformat(),
            "end": now.isoformat(),
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "BAD_REQUEST"


def test_stats_accepts_mixed_naive_and_aware_time_range(indexed_client):
    """带时区 / 不带时区的时间混用:按 UTC 归一,不因裸比较抛 TypeError 落 500。"""
    response = indexed_client.get(
        STATS_URL,
        params={
            "start": "2026-01-01T00:00:00",
            "end": "2027-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["code"] == "OK"


def test_stats_rejects_unknown_bucket_kind(indexed_client):
    """未知分桶粒度:422 VALIDATION_ERROR。"""
    response = indexed_client.get(STATS_URL, params={"bucket": "week"})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_cost_excludes_cached_answers_from_token_usage(settings, upload_dir):
    """答档缓存命中不消耗上游 Token:成本只算真正发生的生成。

    命中答档时生成链路整段跳过(ADR-0013),复制出的查询日志若沿用首发
    请求的 Token 用量,统计会把同一笔 Token 重复计入每次命中,让估算
    成本高于实际支出。
    """
    from app.domain.qa import AnswerContext, GenerationUsage, GeneratorKind
    from app.services.generation import GeneratedAnswer

    class _UsageGenerator:
        kind = GeneratorKind.LLM

        async def generate(self, question, *, context: AnswerContext):
            return GeneratedAnswer(
                text="员工入职满一年后,可以申请年假。",
                usage=GenerationUsage(
                    prompt_tokens=1000, completion_tokens=500, total_tokens=1500
                ),
            )

    app = create_app(
        settings,
        embedder=DeterministicEmbedder(),
        answer_generator=_UsageGenerator(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _index(client, "employee.md", HANDBOOK, tenant="acme")

        first = client.post(CHAT_URL, json={"question": LEAVE_QUESTION})
        assert first.status_code == 200, first.text
        assert first.json()["data"]["cache_hit"] is False

        second = client.post(CHAT_URL, json={"question": LEAVE_QUESTION})
        assert second.status_code == 200, second.text
        assert second.json()["data"]["cache_hit"] is True

        data = client.get(f"{STATS_URL}/cost").json()["data"]

    # 两次问答各留一条日志,但只有首发真正消耗了 Token
    assert data["token_usage"]["prompt_tokens"] == 1000
    assert data["token_usage"]["completion_tokens"] == 500
    assert data["token_usage"]["total_tokens"] == 1500
    # 成本按单次生成折算,不是两次
    assert data["cost"]["total_cost"] == pytest.approx(0.002 * 1 + 0.006 * 0.5)
