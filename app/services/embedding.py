"""向量化编排:校验状态 → 批量向量化 → 写入向量库 → 状态机流转。

与切片同构:失败不静默吞错。但上游失败(未配置密钥 / 限流 / 超时)
与解析、切片不同——它们可重试,不落 failed 状态:文档停在 chunked,
补齐密钥或稍后重试即可(见 ADR-0003)。向量库重嵌即整组替换,
与切片整组替换语义对齐(ADR-0002)。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import structlog

from app.core.errors import AppError, InvalidDocumentStateError, NotFoundError
from app.domain.documents import DocumentRecord, DocumentStatus
from app.domain.repositories import ChunkRepository, DocumentRepository
from app.domain.retrieval import VectorStore
from app.domain.state_machine import ensure_transition
from app.integrations.llm.client import EmbedResult, TokenUsage

logger = structlog.get_logger("embedding")


class Embedder(Protocol):
    """向量模型的窄接缝:统一 LLM 客户端与测试替身都实现该签名。"""

    async def embed(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> EmbedResult: ...


@dataclass(slots=True)
class EmbeddingRunResult:
    """一次向量化请求的结果:生效模型、用量与延迟。"""

    record: DocumentRecord
    model: str
    chunk_count: int
    usage: TokenUsage
    latency_ms: float


class EmbeddingService:
    """把一个已切片文档的全部切片批量向量化并写入向量库。"""

    def __init__(
        self,
        *,
        document_repository: DocumentRepository,
        chunk_repository: ChunkRepository,
        vector_store: VectorStore,
        embedder: Embedder,
    ) -> None:
        self._documents = document_repository
        self._chunks = chunk_repository
        self._vectors = vector_store
        self._embedder = embedder

    async def embed_document(
        self, document_id: str, *, model: str | None = None
    ) -> EmbeddingRunResult:
        """向量化一次:整个文档的切片批量送向量模型,成功后文档落 indexed。"""
        record = self._documents.get(document_id)
        if record is None:
            raise NotFoundError("知识库文档不存在", details={"document_id": document_id})
        if record.status is not DocumentStatus.CHUNKED:
            raise InvalidDocumentStateError(
                details={"document_id": document_id, "status": record.status.value}
            )
        chunks = self._chunks.list_by_document(document_id)
        if not chunks:
            raise InvalidDocumentStateError(
                "文档没有可向量化的切片",
                details={"document_id": document_id, "status": record.status.value},
            )

        started = datetime.now(UTC)
        try:
            result = await self._embedder.embed(
                [chunk.content for chunk in chunks], model=model
            )
        except AppError as exc:
            # 失败侧也留用量与延迟基线;文档停在 chunked,可直接重试(ADR-0003)
            logger.warning(
                "document_embedding_failed",
                document_id=record.id,
                file_name=record.file_name,
                error_code=exc.code.value,
                chunks=len(chunks),
                duration_ms=round((datetime.now(UTC) - started).total_seconds() * 1000, 2),
            )
            raise
        # 并发防护:向量化期间切片被整组替换(重切),本次入库作废,拒绝写入陈旧向量
        current = self._chunks.list_by_document(document_id)
        if (
            record.status is not DocumentStatus.CHUNKED
            or [chunk.chunk_id for chunk in current]
            != [chunk.chunk_id for chunk in chunks]
        ):
            raise InvalidDocumentStateError(
                "文档在向量化期间被重新切片,请对最新切片重试入库",
                details={"document_id": document_id},
            )
        self._vectors.replace(document_id, chunks, result.vectors)

        self._transition(record, DocumentStatus.EMBEDDING)
        self._transition(record, DocumentStatus.INDEXED)
        record.indexed_at = datetime.now(UTC)
        self._documents.save(record)
        logger.info(
            "document_embedded",
            document_id=record.id,
            file_name=record.file_name,
            model=result.model,
            chunks=len(chunks),
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
            latency_ms=result.latency_ms,
            duration_ms=round((record.indexed_at - started).total_seconds() * 1000, 2),
        )
        return EmbeddingRunResult(
            record=record,
            model=result.model,
            chunk_count=len(chunks),
            usage=result.usage,
            latency_ms=result.latency_ms,
        )

    @staticmethod
    def _transition(record: DocumentRecord, target: DocumentStatus) -> None:
        ensure_transition(record.status, target)
        record.status = target
