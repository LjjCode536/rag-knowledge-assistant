"""离线入库链路编排:登记档案 → 落盘 → 状态机流转 → 解析 → 记录结果。

M1 在上传请求内同步解析(教材第 40 章:Loader 只负责读取,工程化归 Service);
M7 引入异步 ingestion 时复用同一服务,状态可查语义不变。
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.core.errors import DocumentParseError, NotFoundError
from app.domain.documents import DocumentElement, DocumentFormat, DocumentRecord, DocumentStatus
from app.domain.repositories import DocumentRepository
from app.domain.state_machine import ensure_transition
from app.integrations.parsing.factory import get_parser
from app.services.storage import LocalDocumentStorage

logger = structlog.get_logger("ingestion")


class IngestionService:
    """上传即解析;解析失败不外抛,落入 failed 状态并记录原因。"""

    def __init__(self, *, repository: DocumentRepository, storage: LocalDocumentStorage) -> None:
        self._repository = repository
        self._storage = storage

    def ingest(
        self,
        *,
        file_name: str,
        content: bytes,
        tenant: str = "default",
        department: str | None = None,
        doc_type: str | None = None,
        version: str = "v1",
    ) -> DocumentRecord:
        """处理一次上传:返回最终档案(parsed 或 failed)。"""
        file_name = _base_name(file_name)
        file_type = DocumentFormat.from_filename(file_name)

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
        self._transition(record, DocumentStatus.PARSING)
        self._storage.save(record.id, file_name=file_name, content=content)

        started = datetime.now(UTC)
        try:
            parser = get_parser(file_type)
            parsed = parser.parse(content, document_id=record.id, file_name=file_name)
        except DocumentParseError as exc:
            # 不静默吞错:状态、原因、日志三处留痕,档案照常可查
            logger.warning(
                "document_parse_failed",
                document_id=record.id,
                file_name=file_name,
                error=str(exc),
            )
            record.error_message = str(exc)
            record.failed_at = datetime.now(UTC)
            self._transition(record, DocumentStatus.FAILED)
            self._repository.save(record)
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
            file_name=file_name,
            file_type=file_type.value,
            elements=record.element_count,
            page_count=record.page_count,
            duration_ms=round((record.parsed_at - started).total_seconds() * 1000, 2),
        )
        return record

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
