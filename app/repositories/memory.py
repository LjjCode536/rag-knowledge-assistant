"""文档与切片仓储的进程内存实现:M2 基线,M3 换 PostgreSQL(六张表)。"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import datetime

from app.domain.chunks import Chunk
from app.domain.documents import DocumentRecord, DocumentStatus
from app.domain.evaluation import EvaluationReport
from app.domain.feedback import FeedbackRecord, FeedbackVerdict
from app.domain.qa import QueryLog
from app.domain.stats import (
    BucketAggregate,
    StatsBucketKind,
    TokenUsageSummary,
    as_utc,
    bucket_start_of,
)


class InMemoryDocumentRepository:
    """线程安全的内存仓储。

    上传端点运行在线程池中,读写必须加锁;
    重启即失忆,生产形态由 M3 的 PostgreSQL 接管。
    """

    def __init__(self) -> None:
        self._records: dict[str, DocumentRecord] = {}
        self._lock = threading.Lock()

    def add(self, record: DocumentRecord) -> None:
        with self._lock:
            if record.id in self._records:
                raise ValueError(f"文档已存在:{record.id}")
            self._records[record.id] = record

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._lock:
            return self._records.get(document_id)

    def save(self, record: DocumentRecord) -> None:
        with self._lock:
            self._records[record.id] = record

    def find_latest_by_file(self, *, tenant: str, file_name: str) -> DocumentRecord | None:
        with self._lock:
            candidates = [
                record
                for record in self._records.values()
                if record.tenant == tenant
                and record.file_name == file_name
                and record.is_latest
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda record: record.created_at)


class InMemoryChunkRepository:
    """切片仓储的内存实现:一个文档一组切片,重切即整组替换。

    上传 / 切片端点运行在线程池中,读写必须加锁;生产形态由 M3 的 PostgreSQL 接管。
    """

    def __init__(self) -> None:
        self._chunks: dict[str, list[Chunk]] = {}
        self._lock = threading.Lock()

    def replace(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        with self._lock:
            self._chunks[document_id] = list(chunks)

    def list_by_document(self, document_id: str) -> list[Chunk]:
        with self._lock:
            return list(self._chunks.get(document_id, []))

    def delete(self, document_id: str) -> None:
        with self._lock:
            self._chunks.pop(document_id, None)


class InMemoryQueryLogRepository:
    """查询日志的内存实现:插入顺序即时间顺序,列表按时间倒序返回。

    问答端点运行在事件循环中,但仍与其它内存仓储保持一致,读写加锁;
    生产形态由 PostgreSQL 的 query_logs 表接管。
    """

    def __init__(self) -> None:
        self._logs: dict[str, QueryLog] = {}
        self._lock = threading.Lock()

    def add(self, log: QueryLog) -> None:
        with self._lock:
            self._logs[log.query_id] = log

    def get(self, query_id: str) -> QueryLog | None:
        with self._lock:
            return self._logs.get(query_id)

    def list_recent(self, *, limit: int) -> list[QueryLog]:
        with self._lock:
            return list(reversed(self._logs.values()))[:limit]

    def count(self) -> int:
        with self._lock:
            return len(self._logs)


class InMemoryFeedbackRepository:
    """回答反馈的内存实现:一问一评(同 feedback_id 覆盖),列表按时间倒序返回。

    问答反馈端点运行在事件循环中,但仍与其它内存仓储保持一致,读写加锁;
    生产形态由 PostgreSQL 的 feedbacks 表接管。
    """

    def __init__(self) -> None:
        self._records: dict[str, FeedbackRecord] = {}
        self._lock = threading.Lock()

    def add(self, record: FeedbackRecord) -> None:
        with self._lock:
            self._records[record.feedback_id] = record

    def get(self, feedback_id: str) -> FeedbackRecord | None:
        with self._lock:
            return self._records.get(feedback_id)

    def get_by_query_id(self, query_id: str) -> FeedbackRecord | None:
        with self._lock:
            for record in reversed(self._records.values()):
                if record.query_id == query_id:
                    return record
        return None

    def list_recent(
        self, *, limit: int, verdict: FeedbackVerdict | None = None
    ) -> list[FeedbackRecord]:
        with self._lock:
            records = [
                record
                for record in reversed(self._records.values())
                if verdict is None or record.verdict == verdict
            ]
        return records[:limit]

    def count(self, *, verdict: FeedbackVerdict | None = None) -> int:
        with self._lock:
            return sum(
                1
                for record in self._records.values()
                if verdict is None or record.verdict == verdict
            )


class InMemoryEvaluationRunRepository:
    """评估运行留档的内存实现:M2 起评估报告已落 docs/iterations 文件,
    内存仓储供测试 / 进程内评估的留档口;生产形态由 PostgreSQL 接管。"""

    def __init__(self) -> None:
        self._runs: dict[str, EvaluationReport] = {}
        self._lock = threading.Lock()

    def add(self, report: EvaluationReport, *, run_id: str) -> None:
        with self._lock:
            if run_id in self._runs:
                raise ValueError(f"评估运行已存在:{run_id}")
            self._runs[run_id] = report

    def count(self) -> int:
        with self._lock:
            return len(self._runs)


class InMemoryStatsRepository:
    """统计聚合的内存实现:在文档 / 切片 / 查询日志三个仓储之上做聚合(T16)。

    与 PostgreSQL 实现同契约:区间口径为闭区间起点 / 开区间终点,
    拒答率与成本折算不在本层(服务层做),本层只数原始量。
    """

    def __init__(
        self,
        *,
        documents: InMemoryDocumentRepository,
        chunks: InMemoryChunkRepository,
        query_logs: InMemoryQueryLogRepository,
    ) -> None:
        self._documents = documents
        self._chunks = chunks
        self._query_logs = query_logs

    def count_documents(self, *, latest_only: bool = True) -> int:
        """文档数;``latest_only`` 只数现行版本(退位 / 删除的版本不计)。"""
        with self._documents._lock:
            records = list(self._documents._records.values())
        if latest_only:
            records = [record for record in records if record.is_latest]
        return len(records)

    def count_indexed_documents(self, *, latest_only: bool = True) -> int:
        """已向量化入库(indexed)的文档数。"""
        with self._documents._lock:
            records = list(self._documents._records.values())
        records = [
            record for record in records if record.status == DocumentStatus.INDEXED
        ]
        if latest_only:
            records = [record for record in records if record.is_latest]
        return len(records)

    def count_chunks(self) -> int:
        """切片总数:全部文档的切片合计。"""
        with self._chunks._lock:
            return sum(len(chunks) for chunks in self._chunks._chunks.values())

    def count_queries(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> int:
        return len(self._logs_in_range(start=start, end=end))

    def count_refusals(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> int:
        return sum(1 for log in self._logs_in_range(start=start, end=end) if log.refused)

    def avg_latency_ms(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> float | None:
        logs = self._logs_in_range(start=start, end=end)
        if not logs:
            return None
        return round(sum(log.latency_ms for log in logs) / len(logs), 2)

    def sum_token_usage(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> TokenUsageSummary:
        logs = self._logs_in_range(start=start, end=end)
        return TokenUsageSummary().accumulate(log.token_usage for log in logs)

    def bucket_queries(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        bucket: StatsBucketKind = StatsBucketKind.DAY,
    ) -> list[BucketAggregate]:
        """按自然小时 / 自然日切桶;没有问答的桶不出现(不补零桶)。"""
        logs = self._logs_in_range(start=start, end=end)
        buckets: dict[datetime, list[QueryLog]] = {}
        for log in logs:
            buckets.setdefault(bucket_start_of(log.created_at, bucket), []).append(log)
        aggregates = []
        for bucket_start in sorted(buckets):
            entries = buckets[bucket_start]
            total_latency = sum(log.latency_ms for log in entries)
            aggregates.append(
                BucketAggregate(
                    bucket_start=bucket_start,
                    bucket_end=bucket_start + bucket.delta,
                    query_count=len(entries),
                    refused_count=sum(1 for log in entries if log.refused),
                    avg_latency_ms=round(total_latency / len(entries), 2),
                    token_usage=TokenUsageSummary().accumulate(
                        log.token_usage for log in entries
                    ),
                )
            )
        return aggregates

    def _logs_in_range(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> list[QueryLog]:
        """区间内的查询日志(闭区间起点 / 开区间终点),按时间升序。"""
        with self._query_logs._lock:
            logs = list(self._query_logs._logs.values())
        selected = [
            log
            for log in logs
            if _in_range(log.created_at, start=start, end=end)
        ]
        return sorted(selected, key=lambda log: log.created_at)


def _in_range(
    moment: datetime, *, start: datetime | None, end: datetime | None
) -> bool:
    """闭区间起点 / 开区间终点的时间归属判定。"""
    normalized = as_utc(moment)
    if start is not None and normalized < start:
        return False
    return not (end is not None and normalized >= end)
