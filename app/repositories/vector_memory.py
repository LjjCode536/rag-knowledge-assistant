"""向量库的进程内存实现:M3 三级存储(内存 → Chroma → pgvector)的第一层基线。

它是 M4 以后一切检索指标的 "before" 基线后端;重启即失忆,
生产形态换 Chroma / pgvector 时只替换本实现,契约不动。
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from app.domain.retrieval import (
    EmbeddedChunk,
    EmbeddingRef,
    Scope,
    ScoredChunk,
    consistent_embedding_ref,
    cosine_similarity,
    matches_scope,
)


class InMemoryVectorStore:
    """线程安全的内存向量库:一个文档一组向量,重嵌即整组替换。

    检索流程:先按来源身份与切片元数据筛候选(模型隔离 + 权限过滤),再逐候选
    算余弦相似度、降序取 top_k;同分按写入顺序稳定排出,保证结果可复现。
    上传 / 切片端点运行在线程池中,读写必须加锁。
    """

    def __init__(self) -> None:
        self._entries: dict[str, list[EmbeddedChunk]] = {}
        self._lock = threading.Lock()

    def replace(self, document_id: str, chunks: Sequence[EmbeddedChunk]) -> None:
        """整组写入一个文档的切片向量;混入不同来源身份的批次视为程序错误。"""
        consistent_embedding_ref(chunks)
        entries = list(chunks)
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
        scope: Scope,
        embedding: EmbeddingRef,
    ) -> list[ScoredChunk]:
        """身份与授权范围过滤都在相似度计算之前:越界向量不进候选。"""
        constraints = scope.effective()
        document_ids = scope.document_ids
        with self._lock:
            candidates = [
                entry
                for entries in self._entries.values()
                for entry in entries
                if entry.embedding == embedding
                and matches_scope(entry.chunk, constraints, document_ids)
            ]
        scored = [
            ScoredChunk(chunk=entry.chunk, score=cosine_similarity(vector, entry.vector))
            for entry in candidates
        ]
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:top_k]

    def vector_of(self, chunk_id: str) -> list[float] | None:
        """内存形态直接查表:切片已被嵌入,向量必然可取。"""
        with self._lock:
            for entries in self._entries.values():
                for entry in entries:
                    if entry.chunk.chunk_id == chunk_id:
                        return list(entry.vector)
        return None

    def identity_counts(self) -> dict[EmbeddingRef | None, int]:
        """库内向量按来源身份计数(启动期一致性告警用)。"""
        counts: dict[EmbeddingRef | None, int] = {}
        with self._lock:
            entries = [entry for batch in self._entries.values() for entry in batch]
        for entry in entries:
            counts[entry.embedding] = counts.get(entry.embedding, 0) + 1
        return counts
