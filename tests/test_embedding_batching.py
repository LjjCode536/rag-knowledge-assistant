"""窄接缝:向量化服务的分批调用(假向量模型,不触网)。

大批文档的切片不能一次性打给上游:请求体过大 / 超时 / 触发上游批量上限。
分批后每批不超过配置上限,批次结果的向量顺序与输入切片顺序严格对齐。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.domain.chunks import Chunk
from app.domain.documents import DocumentRecord, DocumentStatus
from app.integrations.llm.client import EmbedResult, TokenUsage
from app.repositories.memory import (
    InMemoryChunkRepository,
    InMemoryDocumentRepository,
)
from app.repositories.vector_memory import InMemoryVectorStore
from app.services.embedding import EmbeddingService


class RecordingEmbedder:
    """记录每批的输入条数与内容;按批返回可区分的向量。"""

    def __init__(self, dimension: int = 4) -> None:
        self.batches: list[list[str]] = []
        self.dimension = dimension

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> EmbedResult:
        batch = list(texts)
        self.batches.append(batch)
        vectors = [
            [float(len(text)) + float(index) for index in range(self.dimension)]
            for text in batch
        ]
        return EmbedResult(
            vectors=vectors,
            model=model or "fake",
            usage=TokenUsage(prompt_tokens=len(batch), total_tokens=len(batch)),
            latency_ms=1.0,
        )


def _chunks(count: int) -> list[Chunk]:
    return [
        Chunk(
            document_id="doc-1",
            index=index,
            content=f"第 {index} 段内容",
            tenant="acme",
            version="v1",
            element_index_start=index,
            element_index_end=index + 1,
        )
        for index in range(count)
    ]


def _service(settings: Settings, embedder, chunk_count: int) -> EmbeddingService:
    documents = InMemoryDocumentRepository()
    chunks = InMemoryChunkRepository()
    record = DocumentRecord(
        id="doc-1",
        file_name="handbook.txt",
        file_type="txt",
        size_bytes=32,
        status=DocumentStatus.CHUNKED,
    )
    documents.add(record)
    chunks.replace("doc-1", _chunks(chunk_count))
    return EmbeddingService(
        document_repository=documents,
        chunk_repository=chunks,
        vector_store=InMemoryVectorStore(),
        embedder=embedder,
        batch_size=settings.embedding_batch_size,
    )


async def test_large_document_is_embedded_in_batches(settings):
    """切片数超过批上限时按批调用上游,每批不超过上限。"""
    embedder = RecordingEmbedder()
    service = _service(settings, embedder, chunk_count=25)

    await service.embed_document("doc-1")

    assert len(embedder.batches) == 2
    assert [len(batch) for batch in embedder.batches] == [20, 5]


async def test_small_document_is_embedded_in_one_batch(settings):
    """切片数不超过批上限时一次调用(不制造多余请求)。"""
    embedder = RecordingEmbedder()
    service = _service(settings, embedder, chunk_count=3)

    await service.embed_document("doc-1")

    assert len(embedder.batches) == 1
    assert embedder.batches[0] == ["第 0 段内容", "第 1 段内容", "第 2 段内容"]


async def test_batched_vectors_stay_aligned_with_chunks(settings):
    """分批后的向量顺序必须与切片顺序严格对齐(错位即静默数据损坏)。"""
    embedder = RecordingEmbedder()
    service = _service(settings, embedder, chunk_count=25)

    await service.embed_document("doc-1")

    flattened = [text for batch in embedder.batches for text in batch]
    assert flattened == [f"第 {index} 段内容" for index in range(25)]


async def test_batch_size_is_configurable(settings):
    """批大小走配置:换上游限额只改配置,不改代码。"""
    embedder = RecordingEmbedder()
    tuned = settings.model_copy(update={"embedding_batch_size": 4})
    service = _service(tuned, embedder, chunk_count=10)

    await service.embed_document("doc-1")

    assert [len(batch) for batch in embedder.batches] == [4, 4, 2]


async def test_unknown_document_still_raises_before_embedding(settings):
    """文档不存在:不发起任何上游调用。"""
    embedder = RecordingEmbedder()
    service = _service(settings, embedder, chunk_count=3)

    with pytest.raises(NotFoundError):
        await service.embed_document("no-such-document")

    assert embedder.batches == []
