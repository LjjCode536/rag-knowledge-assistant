"""FastAPI 应用装配。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.chunks import router as chunks_router
from app.api.routes.documents import router as documents_router
from app.api.routes.embeddings import router as embeddings_router
from app.api.routes.health import router as health_router
from app.api.routes.search import router as search_router
from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.tracing import TraceIdMiddleware
from app.integrations.llm.client import UnifiedLLMClient
from app.repositories.memory import InMemoryChunkRepository, InMemoryDocumentRepository
from app.repositories.vector_memory import InMemoryVectorStore
from app.services.chunking import ChunkingService
from app.services.embedding import Embedder, EmbeddingService
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService
from app.services.storage import LocalDocumentStorage


def create_app(
    settings: Settings | None = None, *, embedder: Embedder | None = None
) -> FastAPI:
    """建应用;embedder 可注入替身(测试),缺省走统一 LLM 客户端。

    embedder 与文档仓储、切片仓储、向量库一起注入服务层,
    真实上游与假向量替身在主接缝处互换,其余装配零改动。
    """
    settings = settings or get_settings()
    configure_logging(settings)

    llm_client = UnifiedLLMClient(settings)
    effective_embedder = embedder if embedder is not None else llm_client
    document_repository = InMemoryDocumentRepository()
    chunk_repository = InMemoryChunkRepository()
    vector_store = InMemoryVectorStore()
    ingestion_service = IngestionService(
        repository=document_repository,
        storage=LocalDocumentStorage(settings.upload_dir),
    )
    chunking_service = ChunkingService(
        document_repository=document_repository,
        chunk_repository=chunk_repository,
        settings=settings,
    )
    embedding_service = EmbeddingService(
        document_repository=document_repository,
        chunk_repository=chunk_repository,
        vector_store=vector_store,
        embedder=effective_embedder,
    )
    retrieval_service = RetrievalService(
        vector_store=vector_store,
        embedder=effective_embedder,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.llm_client = llm_client
        try:
            yield
        finally:
            await llm_client.aclose()

    # debug 恒为 False:堆栈只进日志,绝不进入响应。
    app = FastAPI(
        title="企业 AI 知识库",
        version=settings.app_version,
        debug=False,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.llm_client = llm_client
    app.state.ingestion_service = ingestion_service
    app.state.chunking_service = chunking_service
    app.state.embedding_service = embedding_service
    app.state.retrieval_service = retrieval_service
    app.state.vector_store = vector_store

    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(chunks_router)
    app.include_router(embeddings_router)
    app.include_router(search_router)
    return app
