"""单元接缝:六张生产表仓储与内存基线的一致性(T13 生产持久化)。

数据仓储(documents / chunks / query_logs / feedbacks / evaluation_runs)
必须与 :mod:`app.repositories.memory` 同语义;并向应用主接缝验证
"重启容器数据仍在" —— 同一 DSN 重开应用,上传 / 日志 / 反馈俱在。
后端不可达(无 Docker)时整批跳过:test_pg_database 夹具负责。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.chunks import Chunk
from app.domain.documents import DocumentRecord, ParsedDocument
from app.domain.evaluation import EvaluationReport, RetrievalMetrics
from app.domain.feedback import FeedbackVerdict
from app.domain.qa import QueryLog
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from app.repositories.memory import (
    InMemoryQueryLogRepository,
)
from app.repositories.postgres import (
    PgChunkRepository,
    PgDatabase,
    PgDocumentRepository,
    PgEvaluationRunRepository,
    PgQueryLogRepository,
)

DOCUMENTS_URL = "/api/v1/documents"
SEARCH_URL = "/api/v1/search"
CHAT_URL = "/api/v1/chat"
QUERY_LOGS_URL = "/api/v1/query-logs"
FEEDBACK_URL = "/api/v1/feedback"

HANDBOOK = """# 员工手册

## 休假制度

员工入职满一年后,可以申请年假;满十年的年假十天。
"""


def _make_db(test_pg_database) -> PgDatabase:
    return PgDatabase(test_pg_database.dsn)


# ===== documents:档案 CRUD 与“最新版本”查询与内存基线同语义 =====


def _document(file_name: str = "handbook.txt", **overrides) -> DocumentRecord:
    return DocumentRecord(
        file_name=file_name,
        file_type="txt",
        size_bytes=32,
        parsed=ParsedDocument(document_id="pending", file_name=file_name, file_type="txt"),
        **overrides,
    )


def test_document_roundtrip_preserves_every_field(test_pg_database):
    """全字段往返:解析产物、告警、版本生命周期与时间戳一个不少。"""
    db = _make_db(test_pg_database)
    repo = PgDocumentRepository(db)
    record = _document(
        tenant="acme",
        department="人力部",
        doc_type="制度",
        version="v2",
        chunk_count=3,
    )

    repo.add(record)
    fetched = repo.get(record.id)

    assert fetched == record
    assert fetched.parsed.file_name == "handbook.txt"
    db.close()


def test_document_get_missing_returns_none(test_pg_database):
    db = _make_db(test_pg_database)
    repo = PgDocumentRepository(db)
    assert repo.get("no-such-document") is None
    db.close()


def test_document_add_duplicate_is_error(test_pg_database):
    db = _make_db(test_pg_database)
    repo = PgDocumentRepository(db)
    record = _document()
    repo.add(record)
    with pytest.raises(ValueError, match="文档已存在"):
        repo.add(record)
    db.close()


def test_document_save_persists_state_transition(test_pg_database):
    """状态机流转后的 save 即整档覆盖:parsed 状态可见、重取同档案。"""
    db = _make_db(test_pg_database)
    repo = PgDocumentRepository(db)
    record = _document()
    repo.add(record)

    record.status = record.status.PARSED
    record.parsed_at = datetime.now(UTC)
    repo.save(record)

    fetched = repo.get(record.id)
    assert fetched.status.value == "parsed"
    assert fetched == record
    db.close()


def test_document_find_latest_by_file_skips_superseded(test_pg_database):
    """(租户 + 文件名)只回现行最新版本;退位版本 / 异租户不参与。"""
    db = _make_db(test_pg_database)
    repo = PgDocumentRepository(db)
    v1 = _document(version="v1", tenant="acme")
    v2 = _document(version="v2", tenant="acme")
    repo.add(v1)
    repo.add(v2)
    # v2 上新 → v1 退位(生产顺序:先退上一版,再登记新版本)
    repo.save(v1.model_copy(update={"is_latest": False}))

    latest = repo.find_latest_by_file(tenant="acme", file_name="handbook.txt")
    assert latest.version == "v2"
    assert latest.tenant == "acme"

    # v2 也退位(如删除)→ 该标识名下不再有现行版本
    repo.save(v2.model_copy(update={"is_latest": False}))
    assert repo.find_latest_by_file(tenant="acme", file_name="handbook.txt") is None
    assert repo.find_latest_by_file(tenant="acme", file_name="missing.txt") is None
    assert repo.find_latest_by_file(tenant="beta", file_name="handbook.txt") is None
    db.close()


# ===== chunks:整组替换语义与内存基线一致 =====


def _chunk(document_id: str, index: int, content: str, **overrides) -> Chunk:
    return Chunk(
        document_id=document_id,
        index=index,
        content=content,
        tenant="acme",
        version="v1",
        element_index_start=0,
        element_index_end=1,
        **overrides,
    )


def test_chunk_replace_list_roundtrip_with_metadata(test_pg_database):
    db = _make_db(test_pg_database)
    repo = PgChunkRepository(db)
    chunks = [
        _chunk("doc-1", 0, "第一段", department="人力部", doc_type="制度"),
        _chunk("doc-1", 1, "第二段", page_number=2, title="休假"),
    ]

    repo.replace("doc-1", chunks)

    fetched = repo.list_by_document("doc-1")
    assert fetched == chunks
    assert [chunk.title for chunk in fetched] == [None, "休假"]
    db.close()


def test_chunk_replace_is_full_swap(test_pg_database):
    """重切即换:旧切片整批作废,新组生效,其他文档不受连带。"""
    db = _make_db(test_pg_database)
    repo = PgChunkRepository(db)
    repo.replace("doc-1", [_chunk("doc-1", 0, "旧一"), _chunk("doc-1", 1, "旧二")])
    repo.replace("doc-2", [_chunk("doc-2", 0, "另册")])

    repo.replace("doc-1", [_chunk("doc-1", 0, "新一")])

    assert [chunk.content for chunk in repo.list_by_document("doc-1")] == ["新一"]
    assert [chunk.content for chunk in repo.list_by_document("doc-2")] == ["另册"]
    db.close()


def test_chunk_list_missing_document_and_delete_idempotent(test_pg_database):
    db = _make_db(test_pg_database)
    repo = PgChunkRepository(db)
    assert repo.list_by_document("never-chunked") == []

    repo.replace("doc-1", [_chunk("doc-1", 0, "待删")])
    repo.delete("doc-1")
    assert repo.list_by_document("doc-1") == []
    repo.delete("doc-1")  # 未入库幂等
    db.close()


# ===== query_logs:追加 / 取回 / 倒序列表 / 计数与内存基线同语义 =====


def _query_log(question: str, *, query_id: str | None = None, **overrides) -> QueryLog:
    return QueryLog(
        query_id=query_id or f"qry_{uuid4().hex[:12]}",
        trace_id="trace-1",
        question=question,
        answer="根据知识库资料,这是答案。",
        refused=False,
        generator="rule_based",
        top_k=3,
        min_score=0.35,
        latency_ms=12.5,
        **overrides,
    )


def test_query_log_roundtrip_preserves_hits_and_usage(test_pg_database):
    db = _make_db(test_pg_database)
    repo = PgQueryLogRepository(db)
    log = _query_log(
        "假期怎么算?",
        refusal_reason=None,
        rewritten_question="年假如何折算",
        expanded_questions=["年假 计算"],
    )

    repo.add(log)
    fetched = repo.get(log.query_id)

    assert fetched == log
    db.close()


def test_query_log_list_recent_is_time_desc_with_count(test_pg_database):
    """时间倒序:与内存基线一致的读取序(created_at 缺省同刻按插入序兜底)。"""
    memory = InMemoryQueryLogRepository()
    db = _make_db(test_pg_database)
    repo = PgQueryLogRepository(db)
    logs = [_query_log(f"问题{i}", query_id=None) for i in range(4)]
    for log in logs:
        memory.add(log)
        repo.add(log)

    assert [log.question for log in repo.list_recent(limit=3)] == [
        log.question for log in memory.list_recent(limit=3)
    ]
    assert repo.count() == memory.count() == 4
    assert repo.get("missing-query-id") is None
    db.close()


def _evaluation_repo(db: PgDatabase) -> PgEvaluationRunRepository:
    return PgEvaluationRunRepository(db)


def _minimal_report() -> EvaluationReport:
    return EvaluationReport(
        run_name="t13-pgvector",
        dataset_name="golden-smoke",
        dataset_version=1,
        k=3,
        top_k=3,
        min_score=0.35,
        chunk_strategy="table_aware",
        chunk_size=500,
        chunk_overlap=50,
        embedder="deterministic",
        mode="in_process",
        command="scripts/evaluate.py",
        cases_total=2,
        summary=RetrievalMetrics(
            recall_at_k=1.0, mrr=1.0, ndcg_at_k=1.0, hit_rate=1.0, cases=2
        ),
    )


def test_evaluation_run_persists_and_counts(test_pg_database):
    db = _make_db(test_pg_database)
    repo = _evaluation_repo(db)
    report = _minimal_report()

    repo.add(report, run_id="run-0001")
    repo.add(report.model_copy(update={"run_name": "再来一次"}), run_id="run-0002")

    with pytest.raises(ValueError, match="评估运行已存在"):
        repo.add(report, run_id="run-0001")
    assert repo.count() == 2
    db.close()


# ===== feedbacks:一问一评 upsert 与内存基线同语义 =====


def _feedback(query_id: str, verdict: str = "useful", **overrides):
    from app.domain.feedback import FeedbackRecord

    return FeedbackRecord(
        query_id=query_id, trace_id="trace-1", verdict=FeedbackVerdict(verdict), **overrides
    )


def test_feedback_repo_matches_memory_semantics(test_pg_database):
    """同 feedback_id 覆盖 / 按 query 取现行反馈 / verdict 过滤与计数。"""
    from app.repositories.memory import InMemoryFeedbackRepository
    from app.repositories.postgres import PgFeedbackRepository

    memory = InMemoryFeedbackRepository()
    db = _make_db(test_pg_database)
    repo = PgFeedbackRepository(db)

    records = [_feedback("qry-1", "useful", comment="命中很准"), _feedback("qry-2", "useless")]
    for record in records:
        memory.add(record)
        repo.add(record)

    assert repo.get(records[0].feedback_id) == memory.get(records[0].feedback_id)
    assert repo.get_by_query_id("qry-2") == memory.get_by_query_id("qry-2")

    # 服务层一问一评:重复提交复用同一 feedback_id(model_copy 保留 id),即覆盖
    again = records[0].model_copy(
        update={"verdict": FeedbackVerdict.USELESS, "comment": "没有回答我的问题"}
    )
    memory.add(again)
    repo.add(again)
    assert repo.get_by_query_id("qry-1") == memory.get_by_query_id("qry-1") == again

    assert repo.list_recent(limit=10) == memory.list_recent(limit=10)
    assert repo.list_recent(limit=10, verdict=FeedbackVerdict.USELESS) == memory.list_recent(
        limit=10, verdict=FeedbackVerdict.USELESS
    )
    assert repo.count() == memory.count() == 2
    assert repo.count(verdict=FeedbackVerdict.USEFUL) == memory.count(
        verdict=FeedbackVerdict.USEFUL
    ) == 0
    assert repo.get("missing-feedback-id") is None
    db.close()


# ===== 应用主接缝:pgvector 装配全链路走通 + 重启数据仍在(验收口径) =====


def _run_full_pipeline(client) -> dict:
    """入库 → 问答 → 日志 → 反馈;返回后续断言需要的键。"""
    upload = client.post(
        DOCUMENTS_URL,
        files={"file": ("employee.md", HANDBOOK.encode(), "text/markdown")},
    )
    assert upload.status_code == 201, upload.text
    response = upload.json()
    assert response["code"] == "OK"
    document_id = response["data"]["id"]

    chunked = client.post(f"{DOCUMENTS_URL}/{document_id}/chunks")
    assert chunked.status_code == 200, chunked.text
    chunks = chunked.json()["data"]["chunks"]

    embedded = client.post(f"{DOCUMENTS_URL}/{document_id}/embeddings")
    assert embedded.status_code == 200, embedded.text

    search = client.post(SEARCH_URL, json={"query": "年假", "top_k": 3})
    assert search.status_code == 200
    assert search.json()["data"]["hits"], "向量检索应命中入库文档"

    chat = client.post(CHAT_URL, json={"question": "入职多久可以申请年假?"})
    assert chat.status_code == 200, chat.text
    answer = chat.json()["data"]
    assert answer["refused"] is False

    feedback = client.post(
        FEEDBACK_URL,
        json={"query_id": answer["query_id"], "verdict": "useful", "comment": "有用"},
    )
    assert feedback.status_code == 200, feedback.text
    return {"document_id": document_id, "chunks": chunks, "query_id": answer["query_id"]}


def test_full_pipeline_over_pgvector_and_restart_survives(settings, test_pg_database):
    """pgvector 装配全链路走通;重开应用(等效重启容器)数据仍在。"""
    pg_settings = settings.model_copy(
        update={"vector_store": "pgvector", "database_url": test_pg_database.dsn}
    )
    from fastapi.testclient import TestClient

    with TestClient(
        create_app(pg_settings, embedder=DeterministicEmbedder()),
        raise_server_exceptions=False,
    ) as client:
        indexed = _run_full_pipeline(client)

    # “重启容器”:全新应用实例挂同库 —— 内存态俱已清零,落库数据必须在
    with TestClient(
        create_app(pg_settings, embedder=DeterministicEmbedder()),
        raise_server_exceptions=False,
    ) as client:
        doc = client.get(f"{DOCUMENTS_URL}/{indexed['document_id']}").json()["data"]
        assert doc["status"] == "indexed"
        assert doc["chunk_count"] == len(indexed["chunks"])

        logs = client.get(QUERY_LOGS_URL, params={"limit": 10}).json()["data"]
        assert logs["total"] == 1
        assert logs["items"][0]["query_id"] == indexed["query_id"]

        listed = client.get(
            FEEDBACK_URL, params={"query_id": indexed["query_id"]}
        ).json()["data"]
        assert listed["total"] == 1
        assert listed["items"][0]["verdict"] == "useful"
        assert listed["items"][0]["comment"] == "有用"


# ===== 建表 / 迁移幂等:重复执行不动既有数据(验收口径) =====


def test_schema_migration_is_idempotent(test_pg_database):
    """ensure_schema 连续执行两次:第二次不报错,既有数据原样保留。"""
    from app.repositories.postgres.schema import ensure_schema

    db = PgDatabase(test_pg_database.dsn)
    try:
        doc_repo = PgDocumentRepository(db)
        chunk_repo = PgChunkRepository(db)
        record = _document()
        doc_repo.add(record)
        chunk_repo.replace("doc-1", [_chunk("doc-1", 0, "存档")])

        with db.connection() as conn:
            ensure_schema(conn)  # 第二次执行(应用重启 / 运维重复迁移)

        assert doc_repo.get(record.id) == record
        assert [c.content for c in chunk_repo.list_by_document("doc-1")] == ["存档"]
    finally:
        db.close()
