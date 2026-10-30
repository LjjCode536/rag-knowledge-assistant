"""文档生命周期编排(教材第 45 章 reindex 流程,T10)。

- **版本退位**:同一标识(租户 + 文件名)的文档上传新版本时,旧版本在上新
  即刻让出"最新"资格——is_latest 置 false、状态迁移到 superseded 终态,
  其向量与关键词通道条目被清理,检索从此只面向最新版本(见 ADR-0009)。
- **文档删除**:状态迁移到 deleted 终态,切片、向量、关键词通道条目与落盘文件
  连带清理;档案保留,终态与失败原因经 GET 档案可查。

失败不静默吞错:清理失败时档案原状保留、原因落 error_message,异常包装为
可重试的 AppError 返回;重试即从未完成的清理步骤重新开始。
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.core.errors import DocumentDeleteError, DocumentSupersedeError, NotFoundError
from app.domain.documents import DocumentRecord, DocumentStatus
from app.domain.repositories import ChunkRepository, DocumentRepository
from app.domain.retrieval import KeywordIndex, VectorStore
from app.domain.state_machine import ensure_transition
from app.services.qa_cache import AnswerCache
from app.services.storage import LocalDocumentStorage

logger = structlog.get_logger("lifecycle")


class DocumentLifecycleService:
    """版本退位与文档删除的编排:清理先行、状态机后置,失败可重试。"""

    def __init__(
        self,
        *,
        document_repository: DocumentRepository,
        chunk_repository: ChunkRepository,
        vector_store: VectorStore,
        keyword_index: KeywordIndex,
        storage: LocalDocumentStorage,
        answer_cache: AnswerCache | None = None,
    ) -> None:
        self._documents = document_repository
        self._chunks = chunk_repository
        self._vectors = vector_store
        self._keyword_index = keyword_index
        self._storage = storage
        self._answer_cache = answer_cache

    def supersede(self, previous: DocumentRecord) -> None:
        """旧版本退位:清理两条检索通道的旧版本数据后,状态迁移到 superseded。

        清理发生在状态翻转之前:清理失败时档案原状保留(仍是 is_latest),
        由调用方以可重试错误回给上传方,重传即重新走本步骤。
        """
        started = datetime.now(UTC)
        try:
            self._cleanup_retrieval_channels(previous.id)
            # 旧版本退出检索面:答档缓存整体失效(新版本入库前旧答档不再可信)
            if self._answer_cache is not None:
                self._answer_cache.clear(reason="document_superseded")
        except Exception as exc:
            self._record_failure(previous, exc)
            raise DocumentSupersedeError(
                details={
                    "document_id": previous.id,
                    "reason": str(exc),
                    "retryable": True,
                }
            ) from exc

        previous.is_latest = False
        previous.error_message = None
        previous.superseded_at = datetime.now(UTC)
        self._transition(previous, DocumentStatus.SUPERSEDED)
        self._documents.save(previous)
        logger.info(
            "document_superseded",
            document_id=previous.id,
            file_name=previous.file_name,
            tenant=previous.tenant,
            duration_ms=round((previous.superseded_at - started).total_seconds() * 1000, 2),
        )

    def delete(self, document_id: str) -> DocumentRecord:
        """删除文档:切片、向量、关键词通道条目与落盘文件连带清理,迁移到 deleted。

        对已删除文档重复调用按幂等处理,返回同一终态档案。
        """
        record = self._documents.get(document_id)
        if record is None:
            raise NotFoundError("知识库文档不存在", details={"document_id": document_id})
        if record.status is DocumentStatus.DELETED:
            return record

        started = datetime.now(UTC)
        try:
            self._cleanup_retrieval_channels(document_id)
            # 检索通道先清:文档内容退出检索面,答档缓存同刻失效(T15)
            if self._answer_cache is not None:
                self._answer_cache.clear(reason="document_deleted")
            # 向量与关键词通道先清:即便后续步骤失败,检索面也不再返回该文档的切片
            self._chunks.delete(document_id)
            self._storage.delete(document_id, file_name=record.file_name)
        except Exception as exc:
            self._record_failure(record, exc)
            raise DocumentDeleteError(
                details={
                    "document_id": record.id,
                    "reason": str(exc),
                    "retryable": True,
                }
            ) from exc

        record.is_latest = False
        record.error_message = None
        # 切片与向量已连带清理:档案的切片摘要同步清零,避免余数误导报表
        record.chunk_count = 0
        record.chunk_warnings = []
        record.deleted_at = datetime.now(UTC)
        self._transition(record, DocumentStatus.DELETED)
        self._documents.save(record)
        logger.info(
            "document_deleted",
            document_id=record.id,
            file_name=record.file_name,
            tenant=record.tenant,
            chunk_count=record.chunk_count,
            duration_ms=round((record.deleted_at - started).total_seconds() * 1000, 2),
        )
        return record

    def _cleanup_retrieval_channels(self, document_id: str) -> None:
        """清理向量与关键词通道的该文档数据;失败由调用方包装为可重试错误。"""
        self._vectors.delete(document_id)
        self._keyword_index.delete(document_id)

    def _record_failure(self, record: DocumentRecord, exc: Exception) -> None:
        """失败留痕:档案状态不动(可重试),原因落 error_message 经 GET 可查。"""
        logger.warning(
            "document_lifecycle_failed",
            document_id=record.id,
            status=record.status.value,
            error=str(exc),
        )
        record.error_message = str(exc)
        self._documents.save(record)

    @staticmethod
    def _transition(record: DocumentRecord, target: DocumentStatus) -> None:
        ensure_transition(record.status, target)
        record.status = target
