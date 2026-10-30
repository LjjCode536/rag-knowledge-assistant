"""FastAPI 应用装配。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.chat import router as chat_router
from app.api.routes.chunks import router as chunks_router
from app.api.routes.documents import router as documents_router
from app.api.routes.embeddings import router as embeddings_router
from app.api.routes.health import router as health_router
from app.api.routes.query_logs import router as query_logs_router
from app.api.routes.search import router as search_router
from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.tracing import TraceIdMiddleware
from app.domain.retrieval import KeywordIndex, QueryPlanner
from app.integrations.llm.client import UnifiedLLMClient
from app.repositories.keyword_memory import InMemoryKeywordIndex
from app.repositories.memory import (
    InMemoryChunkRepository,
    InMemoryDocumentRepository,
    InMemoryQueryLogRepository,
)
from app.repositories.vector_memory import InMemoryVectorStore
from app.services.answering import AnswerService
from app.services.chunking import ChunkingService
from app.services.embedding import Embedder, EmbeddingService
from app.services.generation import AnswerGenerator, RuleBasedAnswerGenerator
from app.services.ingestion import IngestionService
from app.services.query_planning import SiliconFlowQueryPlanner
from app.services.reranking import Reranker, SiliconFlowReranker
from app.services.retrieval import RetrievalService
from app.services.storage import LocalDocumentStorage


def create_app(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
    answer_generator: AnswerGenerator | None = None,
    keyword_index: KeywordIndex | None = None,
    query_planner: QueryPlanner | None = None,
    reranker: Reranker | None = None,
) -> FastAPI:
    """建应用;embedder、生成器与高级检索协作对象可注入替身(测试),缺省走真实实现。

    embedder 与文档仓储、切片仓储、向量库、关键词索引一起注入服务层;
    生成器在问答服务处替换。真实上游与假替身在主接缝处互换,其余装配零改动。
    """
    settings = settings or get_settings()
    configure_logging(settings)

    llm_client = UnifiedLLMClient(settings)
    effective_embedder = embedder if embedder is not None else llm_client
    effective_generator = (
        answer_generator if answer_generator is not None else RuleBasedAnswerGenerator()
    )
    document_repository = InMemoryDocumentRepository()
    chunk_repository = InMemoryChunkRepository()
    vector_store = InMemoryVectorStore()
    effective_keyword_index = keyword_index if keyword_index is not None else InMemoryKeywordIndex()
    effective_planner = (
        query_planner if query_planner is not None else SiliconFlowQueryPlanner(llm_client)
    )
    effective_reranker = reranker if reranker is not None else SiliconFlowReranker(llm_client)
    query_log_repository = InMemoryQueryLogRepository()
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
        keyword_index=effective_keyword_index,
    )
    retrieval_service = RetrievalService(
        vector_store=vector_store,
        embedder=effective_embedder,
        settings=settings,
        keyword_index=effective_keyword_index,
        query_planner=effective_planner,
        reranker=effective_reranker,
    )
    answer_service = AnswerService(
        retrieval_service=retrieval_service,
        document_repository=document_repository,
        query_log_repository=query_log_repository,
        generator=effective_generator,
        settings=settings,
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
    app.state.keyword_index = effective_keyword_index
    app.state.answer_service = answer_service
    app.state.query_log_repository = query_log_repository
    app.state.vector_store = vector_store

    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(chunks_router)
    app.include_router(embeddings_router)
    app.include_router(search_router)
    app.include_router(chat_router)
    app.include_router(query_logs_router)
    return app
