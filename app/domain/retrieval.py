"""检索领域的切片过滤与向量检索契约:M3 三级存储(内存 → Chroma → pgvector)的唯一接缝。

检索层不关心向量如何产生、存在哪里;换后端只替换
:class:`~app.domain.retrieval.VectorStore` 的实现,契约不动(ADR-0001)。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import DimensionMismatchError
from app.domain.chunks import Chunk


class Scope(BaseModel):
    """授权范围:服务端依主体构造的检索前过滤条件,必填且不可缺租户。

    ``tenant`` 是硬边界,没有"不限制"的形态:漏传是程序错误,不是查全库。
    其余字段是调用方可选的收窄条件;未给的字段(None)一律放行,给定字段
    必须与切片元数据完全相等。字段名与 :class:`~app.domain.chunks.Chunk`
    的同名字段保持同名,过滤端按名匹配,改名须两边同步。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str = Field(min_length=1)
    department: str | None = None
    doc_type: str | None = None
    version: str | None = None
    document_id: str | None = None

    def effective(self) -> dict[str, str]:
        """生效的授权范围:剔除未给字段后的字段值对,可直接用于过滤与日志回显。"""
        return self.model_dump(exclude_none=True)


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """检索结果:切片与相似度分数(余弦相似度,越大越相关)。"""

    chunk: Chunk
    score: float


@dataclass(frozen=True, slots=True)
class EmbeddingRef:
    """一组向量的来源身份:模型 / 版本 / 维度。

    三者共同定义一个索引空间。只有身份完全一致的向量才允许互算余弦:
    同维度换模型时余弦分数在数学上仍可算,但语义已不可比,必须按身份隔离
    (ADR-0019)。``version`` 用于同一模型更换预处理 / 指令口径时的重建灰度。
    """

    model: str
    version: str
    dimension: int


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """带来源身份的切片向量:向量库写入的最小单元。"""

    chunk: Chunk
    vector: tuple[float, ...]
    embedding: EmbeddingRef


def build_embedded_chunks(
    chunks: Sequence[Chunk],
    vectors: Sequence[Sequence[float]],
    embedding: EmbeddingRef,
) -> list[EmbeddedChunk]:
    """切片 + 向量 + 来源身份 → 写入单元;维度不符即拒绝,不落库。

    维度在这里而不是在向量库里校验:错误发生在生产方(模型输出与声明不符),
    定位信息(chunk_id / document_id)必须完整带回。
    """
    if len(chunks) != len(vectors):
        raise ValueError(
            f"切片数({len(chunks)})与向量数({len(vectors)})不对齐"
        )
    built: list[EmbeddedChunk] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        if len(vector) != embedding.dimension:
            raise DimensionMismatchError(
                "向量维度与声明的模型维度不一致",
                details={
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "model": embedding.model,
                    "version": embedding.version,
                    "expected_dimension": embedding.dimension,
                    "actual_dimension": len(vector),
                },
            )
        built.append(
            EmbeddedChunk(
                chunk=chunk,
                vector=tuple(float(component) for component in vector),
                embedding=embedding,
            )
        )
    return built


def consistent_embedding_ref(
    embedded: Sequence[EmbeddedChunk],
) -> EmbeddingRef | None:
    """校验一批写入单元同属一个索引空间且维度自洽;空批返回 None。

    向量库实现统一调用本函数:批量写入混入两种身份是程序错误,绝不静默
    按"哪个先到算哪个"落库。
    """
    if not embedded:
        return None
    ref = embedded[0].embedding
    for item in embedded:
        if item.embedding != ref:
            raise ValueError(
                "同一批向量混入了不同的来源身份:"
                f"{ref} / {item.embedding}"
            )
        if len(item.vector) != ref.dimension:
            raise DimensionMismatchError(
                "向量维度与来源身份声明的维度不一致",
                details={
                    "chunk_id": item.chunk.chunk_id,
                    "document_id": item.chunk.document_id,
                    "model": ref.model,
                    "version": ref.version,
                    "expected_dimension": ref.dimension,
                    "actual_dimension": len(item.vector),
                },
            )
    return ref


class VectorStore(Protocol):
    """向量库契约:相似度检索与元数据过滤的存储。

    与切片仓储同语义:同一个文档重嵌即整组替换,旧向量不并存;
    过滤发生在检索之前(先筛候选,再算相似度),而非检索后。
    检索还必须带来源身份:不同模型 / 版本的向量绝不互算分数(ADR-0019)。
    """

    def replace(
        self,
        document_id: str,
        chunks: Sequence[EmbeddedChunk],
    ) -> None:
        """整组写入一个文档的全部切片向量(重嵌即换,旧向量整批作废)。"""
        ...

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        scope: Scope,
        embedding: EmbeddingRef,
    ) -> list[ScoredChunk]:
        """按相似度降序返回至多 top_k 个切片;授权范围与身份隔离在打分之前完成。"""
        ...

    def identity_counts(self) -> dict[EmbeddingRef | None, int]:
        """库内向量按来源身份计数;键 None 表示身份未知(历史行尚未回填)。"""
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
        scope: Scope,
    ) -> list[ScoredChunk]:
        """按词法相关性降序返回至多 top_k 个切片;授权范围过滤在打分之前完成。"""
        ...

    def delete(self, document_id: str) -> None:
        """清理一个文档的全部关键词通道条目(T10 版本退位与删除的连带清理);未入库按幂等处理。"""
        ...


def matches_scope(chunk: Chunk, constraints: dict[str, str]) -> bool:
    """切片元数据逐字段等值匹配:向量库与关键词库共用的检索前过滤实现。"""
    return all(getattr(chunk, field) == value for field, value in constraints.items())


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度:各向量库后端共用的相似度实现(内存基线与 Chroma 同源)。

    向量长度不一致或含零向量按 0 分处理,不参与有效排序。

    计算走 numpy 向量化:候选池规模上去后(MMR 的 O(n²·d) 多样性计算)
    纯 Python 循环会成为瓶颈;口径与数学定义一致,分数逐字可复现。
    """
    if len(a) != len(b) or not a:
        return 0.0
    left = np.asarray(a, dtype=np.float64)
    right = np.asarray(b, dtype=np.float64)
    left_norm = float(np.linalg.norm(left))
    right_norm = float(np.linalg.norm(right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return float(np.dot(left, right) / (left_norm * right_norm))


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
