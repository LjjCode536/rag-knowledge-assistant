"""检索领域的切片过滤与向量检索契约:M3 三级存储(内存 → Chroma → pgvector)的唯一接缝。

检索层不关心向量如何产生、存在哪里;换后端只替换
:class:`~app.domain.retrieval.VectorStore` 的实现,契约不动(ADR-0001)。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
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

    def delete(self, document_id: str) -> None:
        """清理一个文档的全部向量(T10 版本退位与删除的连带清理);未入库按幂等处理。"""
        ...

    def vector_of(self, chunk_id: str) -> Sequence[float] | None:
        """取一个切片的已存向量(MMR 需要候选间的多样性计算);缺失返回 None。"""
        ...


class KeywordIndex(Protocol):
    """关键词通道契约:词法检索与检索前过滤(混合检索的另一半)。

    与 :class:`VectorStore` 同语义:同一个文档重写即整组替换;过滤发生在
    打分之前;分数为词法相关性(BM25),与向量余弦不同尺度,融合时归一。
    """

    def replace(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        """整组写入一个文档的全部切片(重写即换,旧词法条目整批作废)。"""
        ...

    def search(
        self,
        query: str,
        *,
        top_k: int,
        filters: ChunkFilter | None,
    ) -> list[ScoredChunk]:
        """按词法相关性降序返回至多 top_k 个切片;过滤在打分之前完成。"""
        ...

    def delete(self, document_id: str) -> None:
        """清理一个文档的全部关键词通道条目(T10 版本退位与删除的连带清理);未入库按幂等处理。"""
        ...


def matches_chunk_filter(chunk: Chunk, constraints: dict[str, str]) -> bool:
    """切片元数据逐字段等值匹配:向量库与关键词库共用的检索前过滤实现。"""
    return all(getattr(chunk, field) == value for field, value in constraints.items())


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度:各向量库后端共用的相似度实现(内存基线与 Chroma 同源)。

    向量长度不一致或含零向量按 0 分处理,不参与有效排序。
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class Reranker(Protocol):
    """重排契约:用重排模型对召回候选按相关性重新打分排序。

    输入候选保持调用方顺序(供不同上游还原 index),返回按重排分降序、
    分数替换为重排分的切片;上游失败由实现方决定降级或上抛。
    """

    async def rerank(
        self,
        query: str,
        candidates: Sequence[ScoredChunk],
        *,
        top_n: int,
    ) -> list[ScoredChunk]:
        """返回至多 top_n 个切片,按重排相关性降序。"""
        ...


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """一次检索查询的规划产物:改写 + 多查询扩展变体。

    `rewritten` 为 None 表示改写不可用(回退);`variants` 只含去重后
    的非空字符串。原查询始终可用:规划只是增强,不是依赖。
    """

    original: str
    rewritten: str | None = None
    variants: list[str] = field(default_factory=list)


class QueryPlanner(Protocol):
    """查询规划契约:一次产出查询改写与多查询扩展变体(可各自取用)。"""

    async def plan(self, question: str, *, variant_count: int) -> QueryPlan:
        """返回查询规划;实现方保证失败回退而非中断检索。"""
        ...
