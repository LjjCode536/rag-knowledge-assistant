"""切片编排:校验状态 → 策略切分 → 元数据装配 → 质量告警 → 存储与状态机流转。

与解析同构:切片失败不外抛,落入 failed 状态并记录原因;重切即整组替换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from app.core.config import Settings
from app.core.errors import (
    AppError,
    ErrorCode,
    InvalidDocumentStateError,
    NotFoundError,
)
from app.domain.chunks import (
    Chunk,
    ChunkingStrategy,
    ChunkQualityWarning,
    ChunkWarningCode,
)
from app.domain.documents import DocumentRecord, DocumentStatus
from app.domain.repositories import ChunkRepository, DocumentRepository
from app.domain.state_machine import ensure_transition
from app.integrations.chunking.base import RawChunk
from app.integrations.chunking.factory import get_chunker

logger = structlog.get_logger("chunking")

_ALLOWED_SOURCE_STATES = frozenset({DocumentStatus.PARSED, DocumentStatus.CHUNKED})


@dataclass(slots=True)
class ChunkRunResult:
    """一次切片请求的结果:生效参数、切片与告警;失败时切片为空、档案落 failed。"""

    record: DocumentRecord
    strategy: ChunkingStrategy
    chunk_size: int
    chunk_overlap: int
    chunks: list[Chunk] = field(default_factory=list)
    warnings: list[ChunkQualityWarning] = field(default_factory=list)


class ChunkingService:
    """按策略与参数切分已解析的文档;策略与参数每次请求可覆盖,即时生效。"""

    def __init__(
        self,
        *,
        document_repository: DocumentRepository,
        chunk_repository: ChunkRepository,
        settings: Settings,
    ) -> None:
        self._documents = document_repository
        self._chunks = chunk_repository
        self._settings = settings

    def chunk(
        self,
        document_id: str,
        *,
        strategy: ChunkingStrategy | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> ChunkRunResult:
        """切分一次:未给参数时回退统一配置;允许对已切片文档重切。"""
        record = self._documents.get(document_id)
        if record is None:
            raise NotFoundError("知识库文档不存在", details={"document_id": document_id})
        if record.status not in _ALLOWED_SOURCE_STATES or record.parsed is None:
            raise InvalidDocumentStateError(
                details={"document_id": document_id, "status": record.status.value}
            )

        effective_strategy = strategy or self._settings.chunk_strategy
        effective_size = chunk_size or self._settings.chunk_size
        effective_overlap = chunk_overlap or self._settings.chunk_overlap
        if effective_overlap >= effective_size:
            raise AppError(
                "chunk_overlap 必须小于 chunk_size",
                code=ErrorCode.VALIDATION_ERROR,
                status_code=422,
                details={"chunk_size": effective_size, "chunk_overlap": effective_overlap},
            )

        result = ChunkRunResult(
            record=record,
            strategy=effective_strategy,
            chunk_size=effective_size,
            chunk_overlap=effective_overlap,
        )
        started = datetime.now(UTC)
        try:
            self._transition(record, DocumentStatus.CHUNKING)
            raw_chunks = get_chunker(effective_strategy).chunk(
                record.parsed.elements,
                chunk_size=effective_size,
                chunk_overlap=effective_overlap,
            )
        except Exception as exc:
            # 不静默吞错:状态、原因、日志三处留痕,档案照常可查
            logger.warning(
                "document_chunking_failed",
                document_id=record.id,
                strategy=effective_strategy.value,
                error=str(exc),
            )
            record.error_message = str(exc)
            record.failed_at = datetime.now(UTC)
            self._transition(record, DocumentStatus.FAILED)
            self._documents.save(record)
            return result

        result.chunks = self._assemble(record, raw_chunks)
        result.warnings = self._quality_warnings(result.chunks, effective_size)

        self._chunks.replace(document_id, result.chunks)
        record.chunk_count = len(result.chunks)
        record.chunk_warnings = result.warnings
        record.chunked_at = datetime.now(UTC)
        self._transition(record, DocumentStatus.CHUNKED)
        self._documents.save(record)
        logger.info(
            "document_chunked",
            document_id=record.id,
            file_name=record.file_name,
            strategy=effective_strategy.value,
            chunk_size=effective_size,
            chunk_overlap=effective_overlap,
            chunks=len(result.chunks),
            warnings=len(result.warnings),
            duration_ms=round((record.chunked_at - started).total_seconds() * 1000, 2),
        )
        return result

    def get_chunks(self, document_id: str) -> list[Chunk]:
        """查如实存档的切片;未切分(或切片前失败)返回空列表。"""
        return self._chunks.list_by_document(document_id)

    @staticmethod
    def _transition(record: DocumentRecord, target: DocumentStatus) -> None:
        ensure_transition(record.status, target)
        record.status = target

    @staticmethod
    def _assemble(record: DocumentRecord, raw_chunks: list[RawChunk]) -> list[Chunk]:
        """装配切片:补齐租户、部门、文档类型、版本等文档级元数据并连续编号。"""
        return [
            Chunk(
                document_id=record.id,
                index=index,
                content=item.content,
                tenant=record.tenant,
                department=record.department,
                doc_type=record.doc_type,
                version=record.version,
                page_number=item.page_number,
                title=item.title,
                element_index_start=item.element_index_start,
                element_index_end=item.element_index_end,
            )
            for index, item in enumerate(raw_chunks)
        ]

    def _quality_warnings(
        self, chunks: list[Chunk], chunk_size: int
    ) -> list[ChunkQualityWarning]:
        """过短 / 超长告警:告警可见但不让切片失败。"""
        min_chars = self._settings.chunk_min_chars
        too_short = [chunk.index for chunk in chunks if len(chunk.content) < min_chars]
        too_long = [chunk.index for chunk in chunks if len(chunk.content) > chunk_size]
        warnings: list[ChunkQualityWarning] = []
        if too_short:
            warnings.append(
                ChunkQualityWarning(
                    code=ChunkWarningCode.CHUNK_TOO_SHORT,
                    message=f"{len(too_short)} 个切片短于 {min_chars} 字符,建议检查切分参数",
                    details={"count": len(too_short), "threshold": min_chars, "indexes": too_short},
                )
            )
        if too_long:
            warnings.append(
                ChunkQualityWarning(
                    code=ChunkWarningCode.CHUNK_TOO_LONG,
                    message=f"{len(too_long)} 个切片超过 chunk_size {chunk_size},多为不可切的结构",
                    details={
                        "count": len(too_long),
                        "chunk_size": chunk_size,
                        "indexes": too_long,
                    },
                )
            )
        return warnings
