"""检索领域的切片过滤与向量检索契约:M3 三级存储(内存 → Chroma → pgvector)的唯一接缝。

检索层不关心向量如何产生、存在哪里;换后端只替换
:class:`~app.domain.retrieval.VectorStore` 的实现,契约不动(ADR-0001)。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from app.domain.chunks import Chunk


class ChunkFilter(BaseModel):
    """检索前切片元数据过滤条件:权限维度(租户 / 部门)在此生效。

    未给的字段(None)一律放行;给定字段必须与切片元数据完全相等。
    字段名与 :class:`~app.domain.chunks.Chunk` 的切片元数据字段保持同名,
    过滤端按名匹配,改名须两边同步。
    """

    tenant: str | None = None
    department: str | None = None
    doc_type: str | None = None
    version: str | None = None
    document_id: str | None = None

    def effective(self) -> dict[str, str]:
        """生效的过滤条件:剔除未给字段后的字段值对,可直接用于过滤与日志回显。"""
        return self.model_dump(exclude_none=True)


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """检索结果:切片与相似度分数(余弦相似度,越大越相关)。"""

    chunk: Chunk
    score: float


class VectorStore(Protocol):
    """向量库契约:相似度检索与元数据过滤的存储。

    与切片仓储同语义:同一个文档重嵌即整组替换,旧向量不并存;
    过滤发生在检索之前(先筛候选,再算相似度),而非检索后。
    """

    def replace(
        self,
        document_id: str,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        """整组写入一个文档的全部切片向量(重嵌即换,旧向量整批作废)。"""
        ...

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        filters: ChunkFilter | None,
    ) -> list[ScoredChunk]:
        """按相似度降序返回至多 top_k 个切片;过滤在相似度计算之前完成。"""
        ...
