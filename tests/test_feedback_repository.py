"""仓储与服务接缝:反馈的 upsert、按 verdict 统计与差评候选聚合(T11)。

不触网:直接驱动内存仓储与反馈服务,验证 HTTP 端点之下的领域行为。
"""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError
from app.domain.feedback import FeedbackRecord, FeedbackVerdict
from app.domain.qa import GeneratorKind, QueryHit, QueryLog
from app.repositories.memory import (
    InMemoryFeedbackRepository,
    InMemoryQueryLogRepository,
)
from app.services.feedback import FeedbackService


def _query_log(question: str, *, trace_id: str, refused: bool = False) -> QueryLog:
    return QueryLog(
        trace_id=trace_id,
        question=question,
        answer="根据当前资料无法确认。" if refused else "根据知识库资料,这是回答。",
        refused=refused,
        refusal_reason="low_score" if refused else None,
        hits=[]
        if refused
        else [QueryHit(rank=1, chunk_id="chunk_1", document_id="doc_1", score=0.9)],
        top_k=3,
        min_score=0.35,
        latency_ms=1.0,
        generator=GeneratorKind.RULE_BASED,
    )


@pytest.fixture
def service() -> FeedbackService:
    return FeedbackService(
        feedback_repository=InMemoryFeedbackRepository(),
        query_log_repository=InMemoryQueryLogRepository(),
    )


def test_repository_roundtrip_filter_and_count():
    repository = InMemoryFeedbackRepository()
    useful = FeedbackRecord(
        query_id="qry_1", trace_id="t1", verdict=FeedbackVerdict.USEFUL
    )
    useless = FeedbackRecord(
        query_id="qry_2", trace_id="t2", verdict=FeedbackVerdict.USELESS, comment="差评"
    )
    repository.add(useful)
    repository.add(useless)

    assert repository.get(useful.feedback_id) == useful
    assert repository.get("missing") is None
    assert repository.get_by_query_id("qry_2") == useless
    assert repository.get_by_query_id("qry_missing") is None
    assert repository.count() == 2
    assert repository.count(verdict=FeedbackVerdict.USELESS) == 1
    # 最近优先:插入顺序的倒序
    assert [item.feedback_id for item in repository.list_recent(limit=2)] == [
        useless.feedback_id,
        useful.feedback_id,
    ]
    assert [
        item.feedback_id
        for item in repository.list_recent(limit=10, verdict=FeedbackVerdict.USELESS)
    ] == [useless.feedback_id]


def test_submit_links_trace_id_from_query_log(service):
    log = _query_log("入职多久可以申请年假?", trace_id="trace-svc-0001")
    service.query_log_repository.add(log)

    record = service.submit(log.query_id, FeedbackVerdict.USEFUL, comment="很好")

    assert record.trace_id == "trace-svc-0001"
    assert record.query_id == log.query_id


def test_submit_unknown_query_raises_not_found(service):
    with pytest.raises(NotFoundError) as exc_info:
        service.submit("no-such-query", FeedbackVerdict.USEFUL)
    assert exc_info.value.details == {"query_id": "no-such-query"}


def test_submit_upserts_by_query_and_keeps_first_submission_time(service):
    log = _query_log("入职多久可以申请年假?", trace_id="t1")
    service.query_log_repository.add(log)

    first = service.submit(log.query_id, FeedbackVerdict.USEFUL)
    second = service.submit(log.query_id, FeedbackVerdict.USELESS, comment="引用错了")

    assert second.feedback_id == first.feedback_id
    assert second.verdict == FeedbackVerdict.USELESS
    assert second.created_at == first.created_at
    assert service.feedback_repository.count() == 1


def test_negative_candidates_group_by_question(service):
    refused = _query_log("垃圾分类怎么处理?", trace_id="t2", refused=True)
    answered = _query_log("入职多久可以申请年假?", trace_id="t1")
    for log in (refused, answered):
        service.query_log_repository.add(log)
    service.submit(refused.query_id, FeedbackVerdict.USELESS, comment="没答到我关心的")
    service.submit(answered.query_id, FeedbackVerdict.USELESS, comment="太空泛")

    candidates = service.negative_candidates()

    assert len(candidates) == 2
    by_question = {candidate.question: candidate for candidate in candidates}

    refused_candidate = by_question["垃圾分类怎么处理?"]
    assert refused_candidate.failure == "refusal:low_score"
    assert refused_candidate.comments == ["没答到我关心的"]
    assert refused_candidate.origins[0].trace_id == "t2"

    answered_candidate = by_question["入职多久可以申请年假?"]
    assert answered_candidate.failure == "answer_not_useful"
    assert answered_candidate.origins[0].top_documents == ["doc_1"]


def test_negative_candidates_ignore_useful_feedback(service):
    log = _query_log("入职多久可以申请年假?", trace_id="t1")
    service.query_log_repository.add(log)
    service.submit(log.query_id, FeedbackVerdict.USEFUL)

    assert service.negative_candidates() == []


def test_negative_candidates_order_recent_first(service):
    for index in range(3):
        log = _query_log(f"问题{index}?", trace_id=f"t{index}", refused=True)
        service.query_log_repository.add(log)
        service.submit(log.query_id, FeedbackVerdict.USELESS)

    assert [candidate.question for candidate in service.negative_candidates(limit=2)] == [
        "问题2?",
        "问题1?",
    ]


def test_negative_candidates_deterministic_id_and_occurrence(service):
    first = _query_log("入职多久可以申请年假?", trace_id="t1")
    second = _query_log("入职多久可以申请年假?", trace_id="t2")
    for log in (first, second):
        service.query_log_repository.add(log)
        service.submit(log.query_id, FeedbackVerdict.USELESS)

    (candidate,) = service.negative_candidates()

    assert candidate.occurrences == 2
    assert candidate.id.startswith("cand_")
    # first_seen / last_seen 以反馈落库时间为准,与两次提交的实际先后一致
    first_record = service.feedback_repository.get_by_query_id(first.query_id)
    second_record = service.feedback_repository.get_by_query_id(second.query_id)
    assert candidate.first_seen == min(
        first_record.created_at, second_record.created_at
    )
    assert candidate.last_seen == max(first_record.created_at, second_record.created_at)
    # 候选 id 只由问题文本决定:聚合两次结果一致,同一问题跨导出稳定
    assert candidate.id == service.negative_candidates()[0].id
