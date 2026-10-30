"""向量库的进程内存实现:M3 三级存储(内存 → Chroma → pgvector)的第一层基线。

它是 M4 以后一切检索指标的 "before" 基线后端;重启即失忆,
生产形态换 Chroma / pgvector 时只替换本实现,契约不动。
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from app.domain.chunks import Chunk
from app.domain.retrieval import ChunkFilter, ScoredChunk, cosine_similarity, matches_chunk_filter


class InMemoryVectorStore:
    """线程安全的内存向量库:一个文档一组向量,重嵌即整组替换。

    检索流程:先按切片元数据筛候选(权限过滤),再逐候选算余弦相似度、
    降序取 top_k;同分按写入顺序稳定排出,保证结果可复现。
    上传 / 切片端点运行在线程池中,读写必须加锁。
    """

    def __init__(self) -> None:
        self._entries: dict[str, list[tuple[Chunk, list[float]]]] = {}
        self._lock = threading.Lock()

    def replace(
        self,
        document_id: str,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        """整组写入一个文档的切片向量;切片数与向量数不对齐视为程序错误。"""
        if len(chunks) != len(vectors):
            raise ValueError(
                f"切片数({len(chunks)})与向量数({len(vectors)})不对齐:{document_id}"
            )
        entries = [(chunk, list(vector)) for chunk, vector in zip(chunks, vectors, strict=False)]
        with self._lock:
            self._entries[document_id] = entries

    def delete(self, document_id: str) -> None:
        """整批清掉一个文档的全部向量(版本退位 / 删除的连带清理),未入库幂等。"""
        with self._lock:
            self._entries.pop(document_id, None)

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        filters: ChunkFilter | None,
    ) -> list[ScoredChunk]:
        """过滤在相似度计算之前:先取满足条件的候选,再打分排序。"""
        constraints = (filters or ChunkFilter()).effective()
        with self._lock:
            candidates = [
                (chunk, stored)
                for entries in self._entries.values()
                for chunk, stored in entries
                if matches_chunk_filter(chunk, constraints)
            ]
        scored = [
            ScoredChunk(chunk=chunk, score=cosine_similarity(vector, stored))
            for chunk, stored in candidates
        ]
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:top_k]

    def vector_of(self, chunk_id: str) -> list[float] | None:
        """内存形态直接查表:切片已被嵌入,向量必然可取。"""
        with self._lock:
            for entries in self._entries.values():
                for chunk, stored in entries:
                    if chunk.chunk_id == chunk_id:
                        return list(stored)
        return None
