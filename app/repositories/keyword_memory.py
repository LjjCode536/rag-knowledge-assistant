"""关键词通道的进程内存实现:rank_bm25 零件 + 检索前过滤。

分词从 :mod:`app.domain.lexical` 取,与确定性向量替身同口径;
生产形态换生产级关键词索引时只替换本实现,契约不动(ADR-0001)。
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from rank_bm25 import BM25Plus

from app.domain.chunks import Chunk
from app.domain.lexical import text_tokens
from app.domain.retrieval import (
    ChunkFilter,
    ScoredChunk,
    matches_chunk_filter,
)


class InMemoryKeywordIndex:
    """线程安全的内存关键词索引:一个文档一组切片,重写即整组替换。

    检索流程:先按切片元数据筛候选(权限过滤),再对候选子集建 BM25
    打分排序;查询与候选无词面交集时不返回结果。同分按写入顺序稳定排出。
    """

    def __init__(self) -> None:
        self._chunks: dict[str, list[Chunk]] = {}
        self._lock = threading.Lock()

    def replace(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        """整组写入一个文档的全部切片;空列表视为清空该文档。"""
        with self._lock:
            self._chunks[document_id] = list(chunks)

    def search(
        self,
        query: str,
        *,
        top_k: int,
        filters: ChunkFilter | None,
    ) -> list[ScoredChunk]:
        """过滤在打分之前:先取满足条件的候选,再用 BM25 打分排序。"""
        constraints = (filters or ChunkFilter()).effective()
        query_tokens = text_tokens(query)
        if not query_tokens:
            return []
        with self._lock:
            candidates = [
                chunk
                for entries in self._chunks.values()
                for chunk in entries
                if matches_chunk_filter(chunk, constraints)
            ]
        if not candidates:
            return []
        scored = self._bm25_rank(query_tokens, candidates)
        return scored[:top_k]

    def _bm25_rank(
        self, query_tokens: list[str], candidates: list[Chunk]
    ) -> list[ScoredChunk]:
        # 词面交集前置:BM25Plus 的 delta 地板会给语料表内的每个查询词
        # 向所有候选加分(教材式 erratum),零交集的候选也会得正分;
        # 关键词通道契约是「无词面交集不返回」,故先按交集裁剪再打分。
        query_terms = set(query_tokens)
        corpus_tokens = [text_tokens(chunk.content) for chunk in candidates]
        pairs = [
            (chunk, tokens)
            for chunk, tokens in zip(candidates, corpus_tokens, strict=True)
            if query_terms & set(tokens)
        ]
        if not pairs:
            return []
        model = BM25Plus([list(tokens) for _, tokens in pairs])
        scores = model.get_scores(query_tokens)
        ranked = sorted(
            zip([chunk for chunk, _ in pairs], scores.tolist(), strict=True),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [
            ScoredChunk(chunk=chunk, score=score)
            for chunk, score in ranked
            if score > 0.0
        ]
