"""离线入库管线:登记后的文档在一个后台执行体里走完 解析 → 切片 → 向量化。

M7(上,ADR-0012)的异步 ingestion 起步实现:上传请求只登记与落盘,重活由
FastAPI BackgroundTasks 调度本执行体,不引入新的基础设施。各阶段复用与手动
端点完全相同的服务,状态机落点与失败语义一致:

- 解析 / 切片失败 → `failed`(带 error_message),重传即重试(ADR-0009);
- 向量化上游失败 → 按 ADR-0003 不落 failed,停在 `chunked` 可重试;
- 管线自身非预期异常 → 兜底落 `failed` 并带原因,绝不静默吞错;
- 管线进行中档案被退位 / 删除(并发生命周期迁移)→ 让位终态,管线中止。
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from starlette.concurrency import run_in_threadpool

from app.core.errors import AppError, UpstreamError
from app.domain.documents import DocumentRecord, DocumentStatus
from app.domain.state_machine import StateTransitionError
from app.services.chunking import ChunkingService
from app.services.embedding import EmbeddingService
from app.services.ingestion import IngestionService

logger = structlog.get_logger("ingestion_pipeline")


class IngestionPipeline:
    """把一个已登记(uploaded)的文档推进到入库终点的后台执行体。"""

    def __init__(
        self,
        *,
        ingestion_service: IngestionService,
        chunking_service: ChunkingService,
        embedding_service: EmbeddingService,
    ) -> None:
        self._ingestion = ingestion_service
        self._chunking = chunking_service
        self._embedding = embedding_service

    async def run(self, document_id: str) -> None:
        """推进一次登记:解析、切片、向量化(重活在线程池,不阻塞事件循环)。"""
        started = datetime.now(UTC)
        try:
            record = await run_in_threadpool(self._ingestion.parse, document_id)
            if record.status is DocumentStatus.PARSED:
                chunk_run = await run_in_threadpool(self._chunking.chunk, document_id)
                record = chunk_run.record
            if record.status is DocumentStatus.CHUNKED:
                await self._embed_or_note(record)
        except StateTransitionError as exc:
            # 档案在管线进行中被退位 / 删除打到终态:让位,不再推进
            logger.warning(
                "document_ingest_cancelled",
                document_id=document_id,
                reason=str(exc),
            )
            return
        except Exception as exc:
            # 管线非预期异常不静默:落 failed 带原因,重传即重试
            reason = f"后台入库管线意外失败:{exc}"
            self._land_failed(document_id, reason)
            logger.error(
                "document_ingest_crashed",
                document_id=document_id,
                error=str(exc),
            )
            return
        final = self._ingestion.get(document_id)
        logger.info(
            "document_ingest_finished",
            document_id=document_id,
            file_name=final.file_name,
            status=final.status.value,
            duration_ms=round((datetime.now(UTC) - started).total_seconds() * 1000, 2),
        )

    async def _embed_or_note(self, record: DocumentRecord) -> None:
        """向量化一次;上游失败按 ADR-0003 不落地,档案停在 chunked 可重试。"""
        try:
            await self._embedding.embed_document(record.id)
        except UpstreamError as exc:
            logger.warning(
                "document_ingest_upstream_failed",
                document_id=record.id,
                file_name=record.file_name,
                error_code=exc.code.value,
            )
        except AppError as exc:
            # 阶段前置校验拒绝(如空文档没有可向量化切片):不是上游可重试失败,
            # 手动端点回 409 的同一语义在管线内表现为停在 chunked、原因进日志
            logger.warning(
                "document_ingest_stage_refused",
                document_id=record.id,
                file_name=record.file_name,
                error_code=exc.code.value,
            )

    def _land_failed(self, document_id: str, reason: str) -> None:
        """管线非预期异常的兜底落点;状态机冲突(如并发退位竞态)不再落点。"""
        try:
            self._ingestion.fail_with_reason(document_id, reason)
        except StateTransitionError as exc:
            logger.warning(
                "document_ingest_land_conflict",
                document_id=document_id,
                reason=str(exc),
            )
