"""仓储接口:领域层定义契约,基础设施层给实现(ADR-0001)。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.domain.chunks import Chunk
from app.domain.documents import DocumentRecord
from app.domain.evaluation import EvaluationReport
from app.domain.feedback import FeedbackRecord, FeedbackVerdict
from app.domain.qa import QueryLog
from app.domain.stats import BucketAggregate, StatsBucketKind, TokenUsageSummary


class DocumentRepository(Protocol):
    """知识库文档档案的存取契约。

    M1 提供进程内存实现;M3 换 PostgreSQL(六张表之一 documents),契约不变。
    """

    def add(self, record: DocumentRecord) -> None:
        """登记新文档;同 id 重复登记视为程序错误。"""
        ...

    def get(self, document_id: str) -> DocumentRecord | None:
        """按 id 取档案,不存在返回 None。"""
        ...

    def save(self, record: DocumentRecord) -> None:
        """保存档案(状态机流转后的落库)。"""
        ...

    def find_latest_by_file(self, *, tenant: str, file_name: str) -> DocumentRecord | None:
        """按标识(租户 + 文件名)取现行最新版本(is_latest=True);不存在返回 None。

        T10 版本退位的登记依据:同一知识库文档的各版本共用这一标识;
        若出现多余候选(不变量被破坏),取 created_at 最新的一条防御性兜底。
        """
        ...


class ChunkRepository(Protocol):
    """切片仓储的存取契约。

    M2 提供进程内存实现;M3 换 PostgreSQL(六张表之一 chunks),契约不变。
    """

    def replace(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        """整组替换一个文档的切片(重切即换,旧切片整批作废)。"""
        ...

    def list_by_document(self, document_id: str) -> list[Chunk]:
        """按文档取全部切片(含切片元数据),未切分返回空列表。"""
        ...

    def delete(self, document_id: str) -> None:
        """连带清理一个文档的全部切片(文档删除);未入库按幂等处理。"""
        ...


class QueryLogRepository(Protocol):
    """查询日志的存取契约。

    M3 提供进程内存实现;生产形态换 PostgreSQL(六张表之一 query_logs),契约不变。
    """

    def add(self, log: QueryLog) -> None:
        """追加一条查询日志。"""
        ...

    def get(self, query_id: str) -> QueryLog | None:
        """按 query_id 取日志,不存在返回 None。"""
        ...

    def list_recent(self, *, limit: int) -> list[QueryLog]:
        """按时间倒序返回最近至多 limit 条日志。"""
        ...

    def count(self) -> int:
        """返回当前日志总数。"""
        ...


class FeedbackRepository(Protocol):
    """回答反馈的存取契约。

    M4 提供进程内存实现(同 id 覆盖,由反馈服务做按 query 的 upsert);
    生产形态换 PostgreSQL(六张表之外新增 feedbacks 表),契约不变。
    """

    def add(self, record: FeedbackRecord) -> None:
        """落库一条反馈;同 feedback_id 重复落库为覆盖(upsert 语义)。"""
        ...

    def get(self, feedback_id: str) -> FeedbackRecord | None:
        """按 feedback_id 取反馈,不存在返回 None。"""
        ...

    def get_by_query_id(self, query_id: str) -> FeedbackRecord | None:
        """按 query_id 取该次问答的现行反馈(一问一评),不存在返回 None。"""
        ...

    def list_recent(
        self, *, limit: int, verdict: FeedbackVerdict | None = None
    ) -> list[FeedbackRecord]:
        """按时间倒序返回最近至多 limit 条反馈;给 verdict 时只返回该判定。"""
        ...

    def count(self, *, verdict: FeedbackVerdict | None = None) -> int:
        """返回反馈总数;给 verdict 时只统计该判定。"""
        ...


class EvaluationRunRepository(Protocol):
    """评估运行留档的存取契约(六张表之一 evaluation_runs,T13)。

    评估报告照旧落 docs/iterations 的 Markdown + JSON 文件;本契约是同一份
    报告在生产库的留档面(进程内存实现供测试,生产形态走 PostgreSQL)。
    """

    def add(self, report: EvaluationReport, *, run_id: str) -> None:
        """落库一次评估运行;同 run_id 重复落库视为程序错误。"""
        ...

    def count(self) -> int:
        """返回已留档的评估运行总数。"""
        ...


class StatsRepository(Protocol):
    """运营统计与成本统计的聚合契约(T16)。

    统计口径全部落在"已持久化的查询日志"上:问答量、拒答数、平均延迟与
    Token 用量都从查询日志聚合而来,进程重启不丢(memory 模式随进程失忆,
    与向量库口径一致;生产形态走 PostgreSQL 的 query_logs 表)。

    时间区间口径统一为**闭区间起点 / 开区间终点**(:attr:`start` 含,
    :attr:`end` 不含);``None`` 表示该侧不设界(至今全部)。
    """

    def count_documents(self, *, latest_only: bool = True) -> int:
        """返回文档数;``latest_only`` 只数 ``is_latest`` 的现行版本。

        现行版本 = 未退位且未删除的版本(``is_latest=true``);已入库
        (``indexed``)与否不在本口径内 —— 那是
        :meth:`count_indexed_documents` 的事。
        """
        ...

    def count_indexed_documents(self, *, latest_only: bool = True) -> int:
        """返回已向量化入库(indexed)的文档数。

        ``latest_only`` 同样只数 ``is_latest`` 的现行版本。
        """
        ...

    def count_chunks(self) -> int:
        """返回切片总数(全部文档的切片合计)。"""
        ...

    def count_queries(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> int:
        """返回统计区间内的问答量(查询日志条数)。"""
        ...

    def count_refusals(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> int:
        """返回统计区间内的拒答量。"""
        ...

    def avg_latency_ms(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> float | None:
        """返回统计区间内的端到端延迟均值;无问答时返回 None。"""
        ...

    def sum_token_usage(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> TokenUsageSummary:
        """返回统计区间内的 Token 用量合计(prompt / completion / total)。"""
        ...

    def bucket_queries(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        bucket: StatsBucketKind = StatsBucketKind.DAY,
    ) -> list[BucketAggregate]:
        """按分桶粒度返回统计区间内的问答聚合(按桶起点升序)。

        空区间返回空列表;没有问答的桶不出现(不补零桶)。
        """
        ...
