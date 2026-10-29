"""切片器工厂:按策略分发(与解析器工厂同构)。"""

from __future__ import annotations

from app.core.errors import DocumentChunkingError
from app.domain.chunks import ChunkingStrategy
from app.integrations.chunking.base import Chunker
from app.integrations.chunking.splitters import (
    ElementChunker,
    HeadingChunker,
    ParagraphChunker,
    TableAwareChunker,
)

_CHUNKERS: dict[ChunkingStrategy, Chunker] = {
    ChunkingStrategy.PARAGRAPH: ParagraphChunker(),
    ChunkingStrategy.HEADING: HeadingChunker(),
    ChunkingStrategy.TABLE_AWARE: TableAwareChunker(),
    ChunkingStrategy.ELEMENT: ElementChunker(),
}


def get_chunker(strategy: ChunkingStrategy) -> Chunker:
    chunker = _CHUNKERS.get(strategy)
    if chunker is None:  # pragma: no cover — ChunkingStrategy 枚举齐全时不可达
        raise DocumentChunkingError(details={"strategy": strategy.value})
    return chunker
