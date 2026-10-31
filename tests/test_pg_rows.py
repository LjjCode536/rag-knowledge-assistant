"""窄接缝:PostgreSQL 行 → 领域模型的映射(不触网,纯映射层)。

行映射是仓储实现层的细节:领域模型不感知数据库列名。
映射口径:列名与领域字段名同名的直接取;列名不同的按显式别名映射
(chunk_index → index、status → DocumentStatus 等);枚举按值还原。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.chunks import Chunk
from app.domain.documents import (
    DocumentFormat,
    DocumentRecord,
    DocumentStatus,
    ParsedDocument,
)
from app.domain.feedback import FeedbackRecord, FeedbackVerdict
from app.domain.qa import GeneratorKind, QueryLog, RefusalReason
from app.repositories.postgres.rows import (
    chunk_from_row,
    document_from_row,
    feedback_from_row,
    query_log_from_row,
)

CREATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_document_row_maps_every_field():
    row = {
        "id": "doc-1",
        "file_name": "handbook.txt",
        "file_type": "txt",
        "size_bytes": 32,
        "status": "indexed",
        "error_message": None,
        "is_latest": True,
        "superseded_at": None,
        "deleted_at": None,
        "tenant": "acme",
        "department": "人力部",
        "doc_type": "制度",
        "version": "v2",
        "page_count": 3,
        "element_count": 7,
        "parsed": {
            "document_id": "doc-1",
            "file_name": "handbook.txt",
            "file_type": "txt",
            "elements": [],
        },
        "chunk_count": 5,
        "chunk_warnings": [
            {"code": "CHUNK_TOO_SHORT", "message": "切片过短", "details": {"index": 1}}
        ],
        "created_at": CREATED_AT,
        "parsed_at": None,
        "chunked_at": None,
        "indexed_at": CREATED_AT,
        "failed_at": None,
    }

    record = document_from_row(row)

    assert isinstance(record, DocumentRecord)
    assert record.file_type is DocumentFormat.TXT
    assert record.status is DocumentStatus.INDEXED
    assert isinstance(record.parsed, ParsedDocument)
    assert record.chunk_warnings[0].code.value == "CHUNK_TOO_SHORT"
    assert record.created_at == CREATED_AT


def test_document_row_accepts_missing_optional_jsonb():
    row = {
        "id": "doc-1",
        "file_name": "handbook.txt",
        "file_type": "txt",
        "size_bytes": 32,
        "status": "uploaded",
        "error_message": None,
        "is_latest": True,
        "superseded_at": None,
        "deleted_at": None,
        "tenant": "default",
        "department": None,
        "doc_type": None,
        "version": "v1",
        "page_count": None,
        "element_count": None,
        "parsed": None,
        "chunk_count": None,
        "chunk_warnings": None,
        "created_at": CREATED_AT,
        "parsed_at": None,
        "chunked_at": None,
        "indexed_at": None,
        "failed_at": None,
    }

    record = document_from_row(row)

    assert record.parsed is None
    assert record.chunk_warnings == []


def test_chunk_row_maps_chunk_index_to_index():
    row = {
        "chunk_id": "chunk-1",
        "document_id": "doc-1",
        "chunk_index": 4,
        "content": "年假按工龄计算。",
        "tenant": "acme",
        "department": None,
        "doc_type": None,
        "version": "v1",
        "page_number": 2,
        "title": "休假制度",
        "element_index_start": 0,
        "element_index_end": 1,
    }

    chunk = chunk_from_row(row)

    assert isinstance(chunk, Chunk)
    assert chunk.index == 4
    assert chunk.page_number == 2
    assert chunk.title == "休假制度"


def test_query_log_row_maps_enums_and_jsonb():
    row = {
        "query_id": "qry-1",
        "trace_id": "trace-1",
        "question": "年假怎么算?",
        "answer": "按工龄计算。",
        "refused": False,
        "refusal_reason": None,
        "hits": [{"rank": 1, "chunk_id": "c1", "document_id": "d1", "score": 0.9}],
        "filters": {"tenant": "acme"},
        "top_k": 3,
        "min_score": 0.35,
        "latency_ms": 12.5,
        "generator": "rule_based",
        "token_usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        "rewritten_question": None,
        "expanded_questions": [],
        "created_at": CREATED_AT,
    }

    log = query_log_from_row(row)

    assert isinstance(log, QueryLog)
    assert log.generator is GeneratorKind.RULE_BASED
    assert log.refusal_reason is None
    assert log.hits[0].chunk_id == "c1"
    assert log.token_usage.total_tokens == 3
    assert log.filters == {"tenant": "acme"}


def test_query_log_row_maps_refusal_reason():
    row = {
        "query_id": "qry-1",
        "trace_id": "trace-1",
        "question": "年假怎么算?",
        "answer": "根据当前资料无法确认。",
        "refused": True,
        "refusal_reason": "no_hits",
        "hits": [],
        "filters": {},
        "top_k": 3,
        "min_score": 0.35,
        "latency_ms": 1.0,
        "generator": "llm",
        "token_usage": {},
        "rewritten_question": None,
        "expanded_questions": [],
        "created_at": CREATED_AT,
    }

    log = query_log_from_row(row)

    assert log.refusal_reason is RefusalReason.NO_HITS
    assert log.generator is GeneratorKind.LLM


def test_feedback_row_maps_verdict():
    row = {
        "feedback_id": "fb-1",
        "query_id": "qry-1",
        "trace_id": "trace-1",
        "verdict": "useful",
        "comment": "命中很准",
        "created_at": CREATED_AT,
    }

    record = feedback_from_row(row)

    assert isinstance(record, FeedbackRecord)
    assert record.verdict is FeedbackVerdict.USEFUL
    assert record.comment == "命中很准"


@pytest.mark.parametrize(
    "row",
    [
        {"id": "doc-1", "file_type": "unknown-format"},
        {"id": "doc-1", "status": "not-a-status"},
    ],
)
def test_unknown_enum_value_raises(row):
    """库里出现未知枚举值:显式报错,绝不静默降级成默认值。"""
    base = {
        "id": "doc-1",
        "file_name": "handbook.txt",
        "file_type": "txt",
        "size_bytes": 32,
        "status": "uploaded",
        "error_message": None,
        "is_latest": True,
        "superseded_at": None,
        "deleted_at": None,
        "tenant": "default",
        "department": None,
        "doc_type": None,
        "version": "v1",
        "page_count": None,
        "element_count": None,
        "parsed": None,
        "chunk_count": None,
        "chunk_warnings": None,
        "created_at": CREATED_AT,
        "parsed_at": None,
        "chunked_at": None,
        "indexed_at": None,
        "failed_at": None,
    }
    base.update(row)

    with pytest.raises(ValueError):
        document_from_row(base)
