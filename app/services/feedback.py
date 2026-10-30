"""反馈服务:把用户的 verdict 落到查询日志上,并把差评聚成黄金用例候选(T11)。

反馈永远经 ``query_id`` 关联一条已存在的查询日志:trace_id 从日志同源取,
不信任调用方自报;同一 query 的重复提交按 upsert 覆盖(一问一评)。
"""

from __future__ import annotations

import hashlib

from app.core.errors import NotFoundError
from app.domain.feedback import (
    FeedbackOrigin,
    FeedbackRecord,
    FeedbackVerdict,
    GoldenCaseCandidate,
    failure_of,
)
from app.domain.qa import QueryLog
from app.domain.repositories import FeedbackRepository, QueryLogRepository

_CANDIDATE_LIMIT = 50
_TOP_DOCUMENTS = 3


class FeedbackService:
    """反馈闭环的用例层:提交反馈、查差评、产黄金用例候选。"""

    def __init__(
        self,
        *,
        feedback_repository: FeedbackRepository,
        query_log_repository: QueryLogRepository,
    ) -> None:
        self.feedback_repository = feedback_repository
        self.query_log_repository = query_log_repository

    def submit(
        self,
        query_id: str,
        verdict: FeedbackVerdict,
        comment: str | None = None,
    ) -> FeedbackRecord:
        """记录对一次问答的反馈;查询日志不存在则无法关联,视为业务错误。"""
        log = self.query_log_repository.get(query_id)
        if log is None:
            raise NotFoundError(
                "查询日志不存在,无法关联反馈", details={"query_id": query_id}
            )
        existing = self.feedback_repository.get_by_query_id(query_id)
        record = (
            existing.model_copy(update={"verdict": verdict, "comment": comment})
            if existing is not None
            else FeedbackRecord(
                query_id=query_id, trace_id=log.trace_id, verdict=verdict, comment=comment
            )
        )
        self.feedback_repository.add(record)
        return record

    def list_recent(
        self, *, limit: int, verdict: FeedbackVerdict | None = None
    ) -> list[FeedbackRecord]:
        """按时间倒序返回最近反馈;给 verdict 时只返回该判定。"""
        return self.feedback_repository.list_recent(limit=limit, verdict=verdict)

    def get(self, feedback_id: str) -> FeedbackRecord | None:
        """按 feedback_id 取反馈,不存在返回 None。"""
        return self.feedback_repository.get(feedback_id)

    def get_by_query_id(self, query_id: str) -> FeedbackRecord | None:
        """按 query_id 取该次问答的现行反馈(一问一评),不存在返回 None。"""
        return self.feedback_repository.get_by_query_id(query_id)

    def count(self, *, verdict: FeedbackVerdict | None = None) -> int:
        """返回反馈总数;给 verdict 时只统计该判定。"""
        return self.feedback_repository.count(verdict=verdict)

    def negative_candidates(self, *, limit: int = _CANDIDATE_LIMIT) -> list[GoldenCaseCandidate]:
        """把差评按问题聚类成黄金用例候选,频次高 / 时间近的排前面。

        - 频次差评(同问题多次差评)排最前:它们最可能是系统性缺陷;
        - 同一候选下的多条差评合并 comments(去重、按时间正序);
        - 候选 id 由问题文本确定性生成,跨导出稳定,便于人工跟踪与去重。
        """
        total = self.feedback_repository.count(verdict=FeedbackVerdict.USELESS)
        useless = self.feedback_repository.list_recent(
            limit=total, verdict=FeedbackVerdict.USELESS
        )
        # 记录按问题原文聚组:同一问题的差评归并为一个候选
        grouped: dict[str, list[tuple[FeedbackRecord, QueryLog | None]]] = {}
        for record in useless:
            log = self.query_log_repository.get(record.query_id)
            question = log.question if log is not None else record.query_id
            grouped.setdefault(question, []).append((record, log))

        candidates = [
            self._to_candidate(question, entries) for question, entries in grouped.items()
        ]
        ordered = sorted(
            candidates,
            key=lambda candidate: (candidate.occurrences, candidate.last_seen),
            reverse=True,
        )
        return ordered[:limit]

    def _to_candidate(
        self, question: str, entries: list[tuple[FeedbackRecord, QueryLog | None]]
    ) -> GoldenCaseCandidate:
        chronological = sorted(entries, key=lambda entry: entry[0].created_at)
        origins = [self._to_origin(record, log) for record, log in chronological]
        # 记录无查询日志时不该发生,防御性归因为"可回答但没用"
        failures = list(
            dict.fromkeys(
                failure_of(refused=origin.refused, refusal_reason=origin.refusal_reason)
                for origin in origins
            )
        )
        failure = (
            failures[0] if len(failures) == 1 else "mixed" if failures else "answer_not_useful"
        )
        comments = list(
            dict.fromkeys(origin.comment for origin in origins if origin.comment is not None)
        )
        return GoldenCaseCandidate(
            id=f"cand_{hashlib.md5(question.encode("utf-8")).hexdigest()[:12]}",
            question=question,
            occurrences=len(origins),
            failure=failure,
            first_seen=origins[0].created_at,
            last_seen=origins[-1].created_at,
            comments=comments,
            origins=origins,
        )

    def _to_origin(self, record: FeedbackRecord, log: QueryLog | None) -> FeedbackOrigin:
        documents: list[str] = []
        for hit in sorted(log.hits, key=lambda hit: hit.rank) if log else []:
            if hit.document_id not in documents:
                documents.append(hit.document_id)
        return FeedbackOrigin(
            query_id=record.query_id,
            trace_id=record.trace_id,
            verdict=record.verdict,
            refused=log.refused if log else False,
            refusal_reason=log.refusal_reason if log else None,
            top_documents=documents[:_TOP_DOCUMENTS],
            comment=record.comment,
            created_at=record.created_at,
        )
