"""单元接缝:统计聚合在真库上的语义与内存基线一致(T16 生产持久化)。

聚合下推 SQL(query_logs / documents / chunks 三张生产表),区间口径为
闭区间起点 / 开区间终点;应用主接缝验证"重启容器统计不丢"。
后端不可达(无 Docker)时整批跳过:test_pg_database 夹具负责。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.qa import GenerationUsage, GeneratorKind, QueryLog
from app.domain.stats import StatsBucketKind
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from app.repositories.postgres import (
    PgChunkRepository,
    PgDatabase,
    PgDocumentRepository,
    PgQueryLogRepository,
    PgStatsRepository,
)

STATS_URL = "/api/v1/stats"

_BASE = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)


def _log(
    *,
    minutes_ago: int,
    refused: bool = False,
    latency_ms: float = 10.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> QueryLog:
    return QueryLog(
        query_id=f"qry_{uuid4().hex[:12]}",
        trace_id="trace-stats-pg-0001",
        question="入职多久可以申请年假?",
        answer="根据当前资料无法确认。" if refused else "入职满一年可申请年假。",
        refused=refused,
        top_k=3,
        min_score=0.35,
        latency_ms=latency_ms,
        generator=GeneratorKind.LLM,
        token_usage=GenerationUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        created_at=_BASE - timedelta(minutes=minutes_ago),
    )


def _stats_repo(db: PgDatabase) -> PgStatsRepository:
    return PgStatsRepository(db=db)


def test_pg_stats_counts_queries_refusals_and_latency(test_pg_database):
    """问答量 / 拒答数 / 延迟均值与内存基线同语义。"""
    db = PgDatabase(test_pg_database.dsn)
    repo = PgQueryLogRepository(db)
    stats = _stats_repo(db)

    repo.add(_log(minutes_ago=0, latency_ms=10.0))
    repo.add(_log(minutes_ago=1, latency_ms=20.0))
    repo.add(_log(minutes_ago=2, refused=True, latency_ms=30.0))

    assert stats.count_queries() == 3
    assert stats.count_refusals() == 1
    assert stats.avg_latency_ms() == pytest.approx(20.0)
    db.close()


def test_pg_stats_respects_time_range_boundaries(test_pg_database):
    """区间口径:起点闭区间、终点开区间。"""
    db = PgDatabase(test_pg_database.dsn)
    repo = PgQueryLogRepository(db)
    stats = _stats_repo(db)

    repo.add(_log(minutes_ago=0))  # 恰在终点上,不含
    repo.add(_log(minutes_ago=30))  # 恰在起点上,含
    repo.add(_log(minutes_ago=60))  # 起点之前,不含

    assert stats.count_queries(start=_BASE - timedelta(minutes=30), end=_BASE) == 1
    assert stats.count_queries() == 3
    db.close()


def test_pg_stats_sums_token_usage_from_jsonb(test_pg_database):
    """Token 用量从 query_logs.token_usage 的 jsonb 取值并求和。"""
    db = PgDatabase(test_pg_database.dsn)
    repo = PgQueryLogRepository(db)
    stats = _stats_repo(db)

    repo.add(_log(minutes_ago=0, prompt_tokens=1000, completion_tokens=500))
    repo.add(_log(minutes_ago=1, prompt_tokens=2000, completion_tokens=1000))

    usage = stats.sum_token_usage()

    assert usage.prompt_tokens == 3000
    assert usage.completion_tokens == 1500
    assert usage.total_tokens == 4500
    db.close()


def test_pg_stats_buckets_split_by_hour(test_pg_database):
    """小时桶按自然小时切;没有问答的桶不出现。"""
    db = PgDatabase(test_pg_database.dsn)
    repo = PgQueryLogRepository(db)
    stats = _stats_repo(db)

    repo.add(_log(minutes_ago=0))  # 08:00
    repo.add(_log(minutes_ago=10))  # 07:50
    repo.add(_log(minutes_ago=70))  # 06:50

    buckets = stats.bucket_queries(bucket=StatsBucketKind.HOUR)

    assert [b.bucket_start for b in buckets] == [
        datetime(2026, 10, 30, 6, 0, tzinfo=UTC),
        datetime(2026, 10, 30, 7, 0, tzinfo=UTC),
        datetime(2026, 10, 30, 8, 0, tzinfo=UTC),
    ]
    assert [b.query_count for b in buckets] == [1, 1, 1]
    assert buckets[0].bucket_end == datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
    db.close()


def test_pg_stats_daily_bucket_groups_same_day_queries(test_pg_database):
    """日桶按自然日切:同一天的问答合到一个桶。"""
    db = PgDatabase(test_pg_database.dsn)
    repo = PgQueryLogRepository(db)
    stats = _stats_repo(db)

    repo.add(_log(minutes_ago=0))
    repo.add(_log(minutes_ago=120))

    buckets = stats.bucket_queries(bucket=StatsBucketKind.DAY)

    assert len(buckets) == 1
    assert buckets[0].bucket_start == datetime(2026, 10, 30, 0, 0, tzinfo=UTC)
    assert buckets[0].bucket_end == datetime(2026, 10, 31, 0, 0, tzinfo=UTC)
    assert buckets[0].query_count == 2
    db.close()


def test_pg_stats_counts_documents_and_chunks(test_pg_database):
    """规模指标:文档数(只数现行版本)、已入库文档数与切片总数。"""
    from app.domain.chunks import Chunk
    from app.domain.documents import DocumentRecord

    db = PgDatabase(test_pg_database.dsn)
    documents = PgDocumentRepository(db)
    chunks = PgChunkRepository(db)
    stats = _stats_repo(db)

    live = DocumentRecord(
        file_name="handbook.txt", file_type="txt", size_bytes=32, status="indexed"
    )
    retired = DocumentRecord(
        file_name="handbook.txt",
        file_type="txt",
        size_bytes=32,
        status="superseded",
        is_latest=False,
    )
    documents.add(live)
    documents.add(retired)
    chunks.replace(
        live.id,
        [
            Chunk(
                document_id=live.id,
                index=0,
                content="第一段",
                tenant="acme",
                version="v1",
                element_index_start=0,
                element_index_end=1,
            )
        ],
    )

    assert stats.count_documents() == 1
    assert stats.count_documents(latest_only=False) == 2
    assert stats.count_indexed_documents() == 1
    assert stats.count_chunks() == 1
    db.close()


def test_pg_stats_survives_application_restart(settings, test_pg_database):
    """统计来自持久化数据:重开应用(等效重启容器)统计仍在。"""
    from fastapi.testclient import TestClient

    pg_settings = settings.model_copy(
        update={"vector_store": "pgvector", "database_url": test_pg_database.dsn}
    )

    with TestClient(
        create_app(pg_settings, embedder=DeterministicEmbedder()),
        raise_server_exceptions=False,
    ) as client:
        upload = client.post(
            "/api/v1/documents",
            files={
                "file": (
                    "employee.md",
                    "# 休假制度\n\n员工入职满一年后,可以申请年假。\n".encode(),
                    "text/markdown",
                )
            },
        )
        assert upload.status_code == 201, upload.text
        document_id = upload.json()["data"]["id"]
        client.post(f"/api/v1/documents/{document_id}/chunks")
        client.post(f"/api/v1/documents/{document_id}/embeddings")

        chat = client.post("/api/v1/chat", json={"question": "入职多久可以申请年假?"})
        assert chat.status_code == 200, chat.text

        before = client.get(STATS_URL).json()["data"]

    assert before["document_count"] == 1
    assert before["indexed_document_count"] == 1
    assert before["qa"]["query_count"] == 1

    # "重启容器":全新应用实例挂同库,内存态俱已清零,统计必须仍在
    with TestClient(
        create_app(pg_settings, embedder=DeterministicEmbedder()),
        raise_server_exceptions=False,
    ) as client:
        after = client.get(STATS_URL).json()["data"]

    assert after["document_count"] == before["document_count"]
    assert after["indexed_document_count"] == before["indexed_document_count"]
    assert after["chunk_count"] == before["chunk_count"]
    assert after["qa"]["query_count"] == before["qa"]["query_count"]
    assert after["qa"]["avg_latency_ms"] == before["qa"]["avg_latency_ms"]
