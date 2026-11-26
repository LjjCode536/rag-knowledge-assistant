"""文档与切片仓储的进程内存实现:M2 基线,M3 换 PostgreSQL(六张表)。"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import datetime

from app.domain.audit import AuditAction, AuditEvent
from app.domain.chunks import Chunk
from app.domain.documents import DocumentFilter, DocumentRecord, DocumentStatus
from app.domain.evaluation import EvaluationReport
from app.domain.feedback import FeedbackRecord, FeedbackVerdict
from app.domain.grants import DocumentGrant
from app.domain.identity import PrincipalAccount
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

    def list_ids(
        self,
        *,
        tenant: str,
        file_names: Sequence[str] | None = None,
        owner_id: str | None = None,
        latest_only: bool = True,
    ) -> list[str]:
        """按逻辑文档条件取 id;集合语义由调用方使用,不承诺次序。"""
        names = set(file_names) if file_names is not None else None
        with self._lock:
            records = list(self._records.values())
        return [
            record.id
            for record in records
            if record.tenant == tenant
            and (not latest_only or record.is_latest)
            and (names is None or record.file_name in names)
            and (owner_id is None or record.owner_id == owner_id)
        ]

    def list(self, filters: DocumentFilter) -> list[DocumentRecord]:
        """按筛选条件列出档案(T17);口径与 PostgreSQL 实现一致。"""
        with self._lock:
            records = list(self._records.values())
        matched = _match_documents(records, filters)
        # 分页前先排序:created_at 倒序,同刻按 id 兜底(次序确定,不看插入序)
        matched.sort(key=lambda record: (record.created_at, record.id), reverse=True)
        start = filters.offset
        return matched[start : start + filters.limit]

    def count(self, filters: DocumentFilter) -> int:
        """符合筛选条件的档案总数(T17);与 list 同一套筛选条件。"""
        with self._lock:
            records = list(self._records.values())
        return len(_match_documents(records, filters))


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

    def get(
        self, query_id: str, *, tenant: str, principal_id: str | None = None
    ) -> QueryLog | None:
        with self._lock:
            log = self._logs.get(query_id)
        if log is None:
            return None
        return log if _visible(log, tenant=tenant, principal_id=principal_id) else None

    def list_recent(
        self, *, tenant: str, limit: int, principal_id: str | None = None
    ) -> list[QueryLog]:
        with self._lock:
            logs = list(reversed(self._logs.values()))
        return [
            log
            for log in logs
            if _visible(log, tenant=tenant, principal_id=principal_id)
        ][:limit]

    def count(self, *, tenant: str, principal_id: str | None = None) -> int:
        with self._lock:
            logs = list(self._logs.values())
        return sum(
            1 for log in logs if _visible(log, tenant=tenant, principal_id=principal_id)
        )


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

    def get(
        self, feedback_id: str, *, tenant: str, principal_id: str | None = None
    ) -> FeedbackRecord | None:
        with self._lock:
            record = self._records.get(feedback_id)
        if record is None:
            return None
        return record if _visible(record, tenant=tenant, principal_id=principal_id) else None

    def get_by_query_id(
        self, query_id: str, *, tenant: str, principal_id: str | None = None
    ) -> FeedbackRecord | None:
        with self._lock:
            records = list(self._records.values())
        for record in reversed(records):
            if record.query_id == query_id and _visible(
                record, tenant=tenant, principal_id=principal_id
            ):
                return record
        return None

    def list_recent(
        self,
        *,
        tenant: str,
        limit: int,
        verdict: FeedbackVerdict | None = None,
        principal_id: str | None = None,
    ) -> list[FeedbackRecord]:
        with self._lock:
            records = list(reversed(self._records.values()))
        return [
            record
            for record in records
            if _visible(record, tenant=tenant, principal_id=principal_id)
            and (verdict is None or record.verdict == verdict)
        ][:limit]

    def count(
        self,
        *,
        tenant: str,
        verdict: FeedbackVerdict | None = None,
        principal_id: str | None = None,
    ) -> int:
        with self._lock:
            records = list(self._records.values())
        return sum(
            1
            for record in records
            if _visible(record, tenant=tenant, principal_id=principal_id)
            and (verdict is None or record.verdict == verdict)
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

    def count_documents(self, *, tenant: str, latest_only: bool = True) -> int:
        """租户内文档数;``latest_only`` 只数现行版本(退位 / 删除的版本不计)。"""
        with self._documents._lock:
            records = list(self._documents._records.values())
        records = [record for record in records if record.tenant == tenant]
        if latest_only:
            records = [record for record in records if record.is_latest]
        return len(records)

    def count_indexed_documents(self, *, tenant: str, latest_only: bool = True) -> int:
        """租户内已向量化入库(indexed)的文档数。"""
        with self._documents._lock:
            records = list(self._documents._records.values())
        records = [
            record
            for record in records
            if record.tenant == tenant and record.status == DocumentStatus.INDEXED
        ]
        if latest_only:
            records = [record for record in records if record.is_latest]
        return len(records)

    def count_chunks(self, *, tenant: str) -> int:
        """租户内切片总数:只数该租户文档名下的切片。"""
        with self._documents._lock:
            document_ids = {
                record.id
                for record in self._documents._records.values()
                if record.tenant == tenant
            }
        with self._chunks._lock:
            return sum(
                len(chunks)
                for document_id, chunks in self._chunks._chunks.items()
                if document_id in document_ids
            )

    def count_queries(
        self,
        *,
        tenant: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        return len(self._logs_in_range(tenant=tenant, start=start, end=end))

    def count_refusals(
        self,
        *,
        tenant: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        return sum(
            1
            for log in self._logs_in_range(tenant=tenant, start=start, end=end)
            if log.refused
        )

    def avg_latency_ms(
        self,
        *,
        tenant: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float | None:
        logs = self._logs_in_range(tenant=tenant, start=start, end=end)
        if not logs:
            return None
        return round(sum(log.latency_ms for log in logs) / len(logs), 2)

    def sum_token_usage(
        self,
        *,
        tenant: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> TokenUsageSummary:
        logs = self._logs_in_range(tenant=tenant, start=start, end=end)
        return TokenUsageSummary().accumulate(log.token_usage for log in logs)

    def bucket_queries(
        self,
        *,
        tenant: str,
        start: datetime | None = None,
        end: datetime | None = None,
        bucket: StatsBucketKind = StatsBucketKind.DAY,
    ) -> list[BucketAggregate]:
        """按自然小时 / 自然日切桶;没有问答的桶不出现(不补零桶)。"""
        logs = self._logs_in_range(tenant=tenant, start=start, end=end)
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
        self,
        *,
        tenant: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[QueryLog]:
        """租户内区间查询日志(闭区间起点 / 开区间终点),按时间升序。"""
        with self._query_logs._lock:
            logs = list(self._query_logs._logs.values())
        selected = [
            log
            for log in logs
            if log.tenant == tenant and _in_range(log.created_at, start=start, end=end)
        ]
        return sorted(selected, key=lambda log: log.created_at)


def _visible(
    record: QueryLog | FeedbackRecord,
    *,
    tenant: str,
    principal_id: str | None,
) -> bool:
    """读路径可见性:租户是硬边界,``principal_id`` 给出时再收窄到单一主体。"""
    if record.tenant != tenant:
        return False
    return principal_id is None or record.principal_id == principal_id


def _match_documents(
    records: list[DocumentRecord], filters: DocumentFilter
) -> list[DocumentRecord]:
    """按筛选条件过滤档案(T17)。

    口径与 PostgreSQL 实现逐条对齐:

    - ``latest_only`` 只留现行版本(``is_latest``);
    - 租户 / 状态 / 部门 / 文档类型精确匹配;
    - ``keyword`` 命中文件名子串,大小写不敏感;
    - 多个条件按"与"组合。

    **空值归一由服务层负责**(:class:`DocumentCatalogService` 把空白串按
    "未提供"处理),本函数只认"``None`` 表示不限"这一条:``tenant`` 等字段
    传空串会精确匹配空串(匹配不到任何档案)。本函数额外对 ``keyword``
    做 ``strip``,是因为空串关键字在两种实现里都应当退化为"不按关键字过滤"
    —— 否则 PostgreSQL 侧的 ``ILIKE '%%'`` 会匹配全部档案,与内存侧的空串
    语义相左。
    """
    keyword = filters.keyword.strip().lower() if filters.keyword else None
    matched = []
    for record in records:
        if filters.latest_only and not record.is_latest:
            continue
        if filters.tenant is not None and record.tenant != filters.tenant:
            continue
        if filters.status is not None and record.status != filters.status:
            continue
        if filters.department is not None and record.department != filters.department:
            continue
        if filters.doc_type is not None and record.doc_type != filters.doc_type:
            continue
        if (
            filters.document_ids is not None
            and record.id not in filters.document_ids
        ):
            continue
        if keyword is not None and keyword not in record.file_name.lower():
            continue
        matched.append(record)
    return matched


class InMemoryDocumentGrantRepository:
    """文档授权的内存实现:逻辑文档(租户 + 文件名)+ 授权主体唯一。

    授权挂在逻辑文档上,版本退位不删记录;文档删除由生命周期服务连带清理。
    """

    def __init__(self) -> None:
        self._grants: dict[str, DocumentGrant] = {}
        self._lock = threading.Lock()

    def add(self, grant: DocumentGrant) -> None:
        with self._lock:
            for existing_id, existing in list(self._grants.items()):
                if _same_grant(existing, grant):
                    self._grants.pop(existing_id, None)
            self._grants[grant.grant_id] = grant

    def get(self, grant_id: str) -> DocumentGrant | None:
        with self._lock:
            return self._grants.get(grant_id)

    def find(
        self,
        *,
        tenant: str,
        file_name: str,
        subject_type: str,
        subject_id: str | None,
    ) -> DocumentGrant | None:
        with self._lock:
            for grant in self._grants.values():
                if (
                    grant.tenant == tenant
                    and grant.file_name == file_name
                    and grant.subject_type.value == subject_type
                    and grant.subject_id == subject_id
                ):
                    return grant
        return None

    def list_for_logical(self, *, tenant: str, file_name: str) -> list[DocumentGrant]:
        with self._lock:
            grants = list(self._grants.values())
        return sorted(
            (
                grant
                for grant in grants
                if grant.tenant == tenant and grant.file_name == file_name
            ),
            key=lambda grant: grant.created_at,
        )

    def list_relevant(
        self, *, tenant: str, principal_id: str, department: str | None
    ) -> list[DocumentGrant]:
        with self._lock:
            grants = list(self._grants.values())
        return [
            grant
            for grant in grants
            if grant.tenant == tenant
            and (
                grant.subject_type.value == "tenant"
                or (
                    grant.subject_type.value == "user"
                    and grant.subject_id == principal_id
                )
                or (
                    grant.subject_type.value == "department"
                    and department is not None
                    and grant.subject_id == department
                )
            )
        ]

    def delete(self, grant_id: str) -> None:
        with self._lock:
            self._grants.pop(grant_id, None)

    def delete_for_logical(self, *, tenant: str, file_name: str) -> None:
        with self._lock:
            for grant_id, grant in list(self._grants.items()):
                if grant.tenant == tenant and grant.file_name == file_name:
                    self._grants.pop(grant_id, None)


class InMemoryAuditRepository:
    """审计事件的内存实现:只追加,按时间倒序列出;重启随进程失忆。"""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def add(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list(
        self,
        *,
        tenant: str,
        actions: Sequence[AuditAction] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        matched = self._matched(
            tenant=tenant, actions=actions, start=start, end=end
        )
        matched.reverse()
        return matched[offset : offset + limit]

    def count(
        self,
        *,
        tenant: str,
        actions: Sequence[AuditAction] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        return len(
            self._matched(tenant=tenant, actions=actions, start=start, end=end)
        )

    def _matched(
        self,
        *,
        tenant: str,
        actions: Sequence[AuditAction] | None,
        start: datetime | None,
        end: datetime | None,
    ) -> list[AuditEvent]:
        wanted = set(actions or ())
        with self._lock:
            events = list(self._events)
        return [
            event
            for event in events
            if event.tenant == tenant
            and (not wanted or event.action in wanted)
            and _in_range(event.occurred_at, start=start, end=end)
        ]


class InMemoryPrincipalRepository:
    """主体账号的内存实现:测试夹具与无库开发用,重启即失忆。

    仓储本身不创建任何默认账号 —— 种子账号由调用方显式传入;生产形态由
    ``scripts/seed_principals.py`` 显式写入 PostgreSQL(ADR-0020)。
    """

    def __init__(self, accounts: Sequence[PrincipalAccount] = ()) -> None:
        self._by_id: dict[str, PrincipalAccount] = {}
        self._by_username: dict[str, PrincipalAccount] = {}
        self._lock = threading.Lock()
        for account in accounts:
            self.upsert(account)

    def find_by_username(self, username: str) -> PrincipalAccount | None:
        with self._lock:
            return self._by_username.get(username)

    def get(self, principal_id: str) -> PrincipalAccount | None:
        with self._lock:
            return self._by_id.get(principal_id)

    def upsert(self, account: PrincipalAccount) -> None:
        with self._lock:
            username = account.principal.username
            if username is not None:
                owner = self._by_username.get(username)
                if owner is not None and owner.principal.id != account.principal.id:
                    # 与 principals.username 唯一约束同语义:登录名不得歧义
                    raise ValueError(f"登录名已被其他主体占用:{username}")
            existing = self._by_id.get(account.principal.id)
            if existing is not None and existing.principal.username is not None:
                self._by_username.pop(existing.principal.username, None)
            self._by_id[account.principal.id] = account
            if username is not None:
                self._by_username[username] = account

    def list(self) -> list[PrincipalAccount]:
        with self._lock:
            return sorted(self._by_id.values(), key=lambda item: item.principal.id)


def _same_grant(left: DocumentGrant, right: DocumentGrant) -> bool:
    """同逻辑文档 + 同授权主体即视为同一条授权(重复授权幂等)。"""
    return (
        left.tenant == right.tenant
        and left.file_name == right.file_name
        and left.subject_type == right.subject_type
        and left.subject_id == right.subject_id
    )


def _in_range(
    moment: datetime, *, start: datetime | None, end: datetime | None
) -> bool:
    """闭区间起点 / 开区间终点的时间归属判定;两端与时刻统一按 UTC 比较。"""
    normalized = as_utc(moment)
    if start is not None and normalized < as_utc(start):
        return False
    return not (end is not None and normalized >= as_utc(end))
