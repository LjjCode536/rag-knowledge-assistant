"""文档与切片仓储的进程内存实现:M2 基线,M3 换 PostgreSQL(六张表)。"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from app.domain.chunks import Chunk
from app.domain.documents import DocumentRecord
from app.domain.evaluation import EvaluationReport
from app.domain.feedback import FeedbackRecord, FeedbackVerdict
from app.domain.qa import QueryLog


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
