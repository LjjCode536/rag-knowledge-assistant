"""向量来源身份与维度守卫:写入落身份、检索按身份隔离、维度不符即拒绝(ADR-0019)。

全部走服务层与内存向量库的窄接缝,不触网、不依赖 PostgreSQL。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.core.config import Settings
from app.core.errors import DimensionMismatchError
from app.domain.chunks import Chunk
from app.domain.documents import DocumentRecord, DocumentStatus
from app.domain.retrieval import (
    EmbeddingRef,
    Scope,
    build_embedded_chunks,
)
from app.integrations.llm.client import EmbedResult, TokenUsage
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from app.repositories.memory import InMemoryChunkRepository, InMemoryDocumentRepository
from app.repositories.vector_memory import InMemoryVectorStore
from app.services.embedding import EmbeddingService
from app.services.retrieval import RetrievalService
from tests.support import authorized_client, json_log_entries

_DIM = 4
_REF = EmbeddingRef(model="fake", version="v1", dimension=_DIM)


class FixedEmbedder:
    """固定维度的向量替身;维度可调,用来触发写入 / 查询的维度守卫。"""

    def __init__(self, dim: int = _DIM) -> None:
        self.dim = dim
        self.batches: list[list[str]] = []

    async def embed(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> EmbedResult:
        self.batches.append(list(texts))
        return EmbedResult(
            vectors=[[1.0] * self.dim for _ in texts],
            model="fake",
            usage=TokenUsage(prompt_tokens=len(texts), total_tokens=len(texts)),
            latency_ms=0.0,
        )


def _chunk(index: int = 0, *, document_id: str = "doc-1") -> Chunk:
    return Chunk(
        document_id=document_id,
        index=index,
        content=f"第 {index} 段内容",
        tenant="acme",
        version="v1",
        element_index_start=index,
        element_index_end=index + 1,
    )


def _seed_service(
    embedder: FixedEmbedder, *, ref: EmbeddingRef = _REF, chunk_count: int = 1
) -> tuple[EmbeddingService, InMemoryVectorStore, InMemoryDocumentRepository]:
    documents = InMemoryDocumentRepository()
    chunks = InMemoryChunkRepository()
    documents.add(
        DocumentRecord(
            id="doc-1",
            file_name="handbook.txt",
            file_type="txt",
            size_bytes=32,
            status=DocumentStatus.CHUNKED,
        )
    )
    chunks.replace("doc-1", [_chunk(index) for index in range(chunk_count)])
    store = InMemoryVectorStore()
    service = EmbeddingService(
        document_repository=documents,
        chunk_repository=chunks,
        vector_store=store,
        embedder=embedder,
        embedding=ref,
        batch_size=20,
    )
    return service, store, documents


async def test_embedded_vectors_are_stamped_with_source_identity() -> None:
    """写入即落身份:检索侧按身份过滤才有依据(ADR-0019 的写入半场)。"""
    service, store, documents = _seed_service(FixedEmbedder(), chunk_count=3)

    await service.embed_document("doc-1")

    assert store.identity_counts() == {_REF: 3}
    assert documents.get("doc-1").status is DocumentStatus.INDEXED


async def test_dimension_mismatch_rejects_the_whole_document_before_write() -> None:
    """模型输出维度与声明不符:整篇拒绝,半条向量都不落库,文档停在 chunked。"""
    embedder = FixedEmbedder(dim=_DIM - 1)
    service, store, documents = _seed_service(embedder, chunk_count=3)

    with pytest.raises(DimensionMismatchError) as caught:
        await service.embed_document("doc-1")

    assert caught.value.details["expected_dimension"] == _DIM
    assert caught.value.details["actual_dimension"] == _DIM - 1
    assert caught.value.details["document_id"] == "doc-1"
    assert store.identity_counts() == {}
    assert documents.get("doc-1").status is DocumentStatus.CHUNKED


def test_build_embedded_chunks_rejects_wrong_dimension() -> None:
    with pytest.raises(DimensionMismatchError):
        build_embedded_chunks([_chunk()], [[1.0, 0.0]], _REF)


def test_store_rejects_mixed_identity_in_one_batch() -> None:
    """同一批写入混入两种身份是程序错误,不能静默按先到者落库。"""
    other = EmbeddingRef(model="other-model", version="v1", dimension=_DIM)
    mixed = build_embedded_chunks([_chunk(0)], [[1.0] * _DIM], _REF) + build_embedded_chunks(
        [_chunk(1)], [[1.0] * _DIM], other
    )

    with pytest.raises(ValueError):
        InMemoryVectorStore().replace("doc-1", mixed)


async def test_retrieval_rejects_query_vector_of_wrong_dimension() -> None:
    """查询向量维度与索引声明不符是配置错配,不能伪装成"没有相关内容"。"""
    settings = Settings(
        _env_file=None,
        environment="test",
        embedding_model=_REF.model,
        embedding_version=_REF.version,
        embedding_dimension=_DIM,
        use_query_rewrite=False,
        use_multi_query=False,
        use_hybrid=False,
        use_mmr=False,
        use_rerank=False,
    )
    service = RetrievalService(
        vector_store=InMemoryVectorStore(),
        embedder=FixedEmbedder(dim=_DIM + 1),
        settings=settings,
    )

    with pytest.raises(DimensionMismatchError):
        await service.search("任意问题", top_k=1, scope=Scope(tenant="acme"))


async def test_retrieval_never_sees_rows_from_another_identity() -> None:
    """同维度换模型:旧向量不进入候选,结果为空而不是混算降分。"""
    store = InMemoryVectorStore()
    store.replace(
        "doc-old",
        build_embedded_chunks(
            [_chunk(0, document_id="doc-old")],
            [[1.0, 0.0, 0.0, 0.0]],
            EmbeddingRef(model="old-model", version="v1", dimension=_DIM),
        ),
    )
    settings = Settings(
        _env_file=None,
        environment="test",
        embedding_model=_REF.model,
        embedding_version=_REF.version,
        embedding_dimension=_DIM,
        use_query_rewrite=False,
        use_multi_query=False,
        use_hybrid=False,
        use_mmr=False,
        use_rerank=False,
    )
    service = RetrievalService(
        vector_store=store,
        embedder=FixedEmbedder(),
        settings=settings,
    )

    assert await service.search("任意问题", top_k=5, scope=Scope(tenant="acme")) == []


def test_startup_warns_about_stale_identity(settings, capsys) -> None:
    """启动盘点:库里存在非当前身份的向量时大声告警,但不阻断启动(ADR-0019)。"""
    store = InMemoryVectorStore()
    store.replace(
        "doc-old",
        build_embedded_chunks(
            [_chunk(0, document_id="doc-old")],
            [[1.0, 0.0]],
            EmbeddingRef(model="old-model", version="v1", dimension=2),
        ),
    )
    app = create_app(settings, embedder=DeterministicEmbedder(), vector_store=store)

    with authorized_client(app):
        pass

    entries = [
        entry
        for entry in json_log_entries(capsys.readouterr().out)
        if entry["event"] == "embedding_identity_mismatch"
    ]
    assert len(entries) == 1
    assert entries[0]["stale"] == [
        {"model": "old-model", "version": "v1", "dimension": 2, "count": 1}
    ]
    assert entries[0]["current"]["model"] == settings.embedding_model


def test_filter_is_identity_scoped_too() -> None:
    """身份过滤与元数据过滤叠加:别的身份即使租户匹配也不出现。"""
    store = InMemoryVectorStore()
    store.replace(
        "doc-old",
        build_embedded_chunks(
            [_chunk(0, document_id="doc-old")],
            [[1.0, 0.0, 0.0, 0.0]],
            EmbeddingRef(model="old-model", version="v1", dimension=_DIM),
        ),
    )

    assert (
        store.search(
            [1.0, 0.0, 0.0, 0.0],
            top_k=5,
            scope=Scope(tenant="acme"),
            embedding=_REF,
        )
        == []
    )
