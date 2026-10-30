"""离线入库链路编排:登记档案 → 落盘 → 状态机流转 → 解析 → 记录结果。

M1 在上传请求内同步解析;M7(上)把"登记"与"解析"拆成两个阶段(见 ADR-0012):
上传请求只做 register(校验格式、同标识旧版本退位、登记与落盘)立即返回任务句柄,
parse 转后台任务执行;`ingestion_async=false` 时两条阶段仍串行在请求内。
两条形态共用同一服务与同一状态机落点,行为一致。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import structlog

from app.core.errors import DocumentParseError, NotFoundError
from app.domain.documents import DocumentElement, DocumentFormat, DocumentRecord, DocumentStatus
from app.domain.repositories import DocumentRepository
from app.domain.state_machine import ensure_transition
from app.integrations.parsing.factory import get_parser
from app.services.lifecycle import DocumentLifecycleService
from app.services.storage import LocalDocumentStorage

logger = structlog.get_logger("ingestion")

# 已进程推进到终态的档案不得被兜底落点改写:失败口径只作用于进行中的中间态
_NOT_LANDABLE = frozenset(
    {DocumentStatus.FAILED, DocumentStatus.INDEXED, DocumentStatus.SUPERSEDED,
     DocumentStatus.DELETED}
)


class IngestionService:
    """登记 → 解析两阶段的编排器;解析失败不外抛,落入 failed 状态并记录原因。

    同一标识(租户 + 文件名)的新版本在上新即让旧版本退位并清理其
    向量与关键词通道条目,检索面只保留最新版本的入库结果(见 ADR-0009)。
    """

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        storage: LocalDocumentStorage,
        lifecycle_service: DocumentLifecycleService,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._lifecycle_service = lifecycle_service
        # 并发上传同一标识(租户 + 文件名)时,身份查找 → 退位 → 登记必须串行:
        # 这是一个进程内的关键段,M3 生产形态由数据库事务接管同一语义(ADR-0009)
        self._version_lock = threading.Lock()

    def register(
        self,
        *,
        file_name: str,
        content: bytes,
        tenant: str = "default",
        department: str | None = None,
        doc_type: str | None = None,
        version: str = "v1",
    ) -> DocumentRecord:
        """登记一次上传:校验格式、旧版本退位、登记档案并落盘,停在 uploaded。

        立即可返回;解析及后续处理由调用方调度(后台管线或请求内同步)。
        """
        file_name = _base_name(file_name)
        file_type = DocumentFormat.from_filename(file_name)

        # 同标识(租户 + 文件名)的旧版本在上新即退位;退位失败不落残档——
        # 新档案此刻尚未登记,旧档案原状保留,重传即从退位步骤重试(ADR-0009)
        with self._version_lock:
            previous = self._repository.find_latest_by_file(
                tenant=tenant, file_name=file_name
            )
            if previous is not None:
                self._lifecycle_service.supersede(previous)

            record = DocumentRecord(
                file_name=file_name,
                file_type=file_type,
                size_bytes=len(content),
                tenant=tenant,
                department=department,
                doc_type=doc_type,
                version=version,
            )
            self._repository.add(record)
        self._storage.save(record.id, file_name=file_name, content=content)
        return record

    def parse(self, document_id: str) -> DocumentRecord:
        """处理一次登记:解析回落盘文件,成功落 parsed、失败落 failed 并记录原因。

        返回档案本身,不外抛解析失败;进度迁移即时可见,进度查询不看到"未推进"。
        """
        record = self.get(document_id)
        self._transition(record, DocumentStatus.PARSING)
        self._repository.save(record)

        started = datetime.now(UTC)
        try:
            content = self._storage.load(record.id, file_name=record.file_name)
            parser = get_parser(record.file_type)
            parsed = parser.parse(content, document_id=record.id, file_name=record.file_name)
        except (DocumentParseError, OSError) as exc:
            # 不静默吞错:状态、原因、日志三处留痕,档案照常可查
            logger.warning(
                "document_parse_failed",
                document_id=record.id,
                file_name=record.file_name,
                error=str(exc),
            )
            self._save_failed(record, str(exc))
            return record

        record.parsed = parsed
        record.page_count = parsed.page_count
        record.element_count = len(parsed.elements)
        record.parsed_at = datetime.now(UTC)
        self._transition(record, DocumentStatus.PARSED)
        self._repository.save(record)
        logger.info(
            "document_parsed",
            document_id=record.id,
            file_name=record.file_name,
            file_type=record.file_type.value,
            elements=record.element_count,
            page_count=record.page_count,
            duration_ms=round((record.parsed_at - started).total_seconds() * 1000, 2),
        )
        return record

    def _save_failed(self, record: DocumentRecord, reason: str) -> None:
        """failed 落点公共收口:原因入档案、failed_at 留痕、状态机迁移与落库。"""
        record.error_message = reason
        record.failed_at = datetime.now(UTC)
        self._transition(record, DocumentStatus.FAILED)
        self._repository.save(record)

    def fail_with_reason(self, document_id: str, reason: str) -> bool:
        """把进行中的档案落 failed 并记录原因(后台管线的兜底落点)。

        终态档案(failed / indexed / superseded / deleted)不动,返回 False;
        这保证管线外的生命周期迁移与人工操作不会被管线错误覆盖。
        """
        record = self.get(document_id)
        if record.status in _NOT_LANDABLE:
            logger.warning(
                "document_ingest_land_skipped",
                document_id=document_id,
                file_name=record.file_name,
                status=record.status.value,
            )
            return False
        self._save_failed(record, reason)
        return True

    def get(self, document_id: str) -> DocumentRecord:
        """查档案;不存在抛 404。"""
        record = self._repository.get(document_id)
        if record is None:
            raise NotFoundError("知识库文档不存在", details={"document_id": document_id})
        return record

    def get_elements(self, document_id: str) -> list[DocumentElement]:
        """查解析产物;未成功解析(或空文档)返回空列表。"""
        record = self.get(document_id)
        return record.parsed.elements if record.parsed else []

    @staticmethod
    def _transition(record: DocumentRecord, target: DocumentStatus) -> None:
        ensure_transition(record.status, target)
        record.status = target


def _base_name(file_name: str) -> str:
    """只保留文件名,防御客户端传路径。"""
    return file_name.replace("\\", "/").rsplit("/", 1)[-1]
