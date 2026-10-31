"""FastAPI 应用装配。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import FastAPI

from app.api.routes.chat import router as chat_router
from app.api.routes.chunks import router as chunks_router
from app.api.routes.documents import router as documents_router
from app.api.routes.embeddings import router as embeddings_router
from app.api.routes.feedback import router as feedback_router
from app.api.routes.health import router as health_router
from app.api.routes.query_logs import router as query_logs_router
from app.api.routes.search import router as search_router
from app.api.routes.stats import router as stats_router
from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.tracing import TraceIdMiddleware
from app.domain.repositories import (
    ChunkRepository,
    DocumentRepository,
    FeedbackRepository,
    QueryLogRepository,
    StatsRepository,
)
from app.domain.retrieval import KeywordIndex, QueryPlanner, VectorStore
from app.integrations.llm.client import UnifiedLLMClient
from app.repositories.keyword_memory import InMemoryKeywordIndex
from app.repositories.memory import (
    InMemoryChunkRepository,
    InMemoryDocumentRepository,
    InMemoryFeedbackRepository,
    InMemoryQueryLogRepository,
    InMemoryStatsRepository,
)
from app.repositories.vector_memory import InMemoryVectorStore
from app.services.answering import AnswerService
from app.services.chunking import ChunkingService
from app.services.embedding import Embedder, EmbeddingService
from app.services.feedback import FeedbackService
from app.services.generation import (
    AnswerGenerator,
    RuleBasedAnswerGenerator,
    SiliconFlowAnswerGenerator,
)
from app.services.ingestion import IngestionService
from app.services.ingestion_pipeline import IngestionPipeline
from app.services.lifecycle import DocumentLifecycleService
from app.services.qa_cache import AnswerCache
from app.services.query_planning import SiliconFlowQueryPlanner
from app.services.reranking import Reranker, SiliconFlowReranker
from app.services.retrieval import RetrievalService
from app.services.stats import StatsService
from app.services.storage import LocalDocumentStorage

if TYPE_CHECKING:
    from app.repositories.postgres import PgDatabase


def _build_answer_generator(
    settings: Settings, llm_client: UnifiedLLMClient
) -> AnswerGenerator:
    """按配置选回答生成器:rule = 规则版基线,llm = 真实 LLM 有据生成。"""
    if settings.qa_generator == "rule":
        return RuleBasedAnswerGenerator()
    return SiliconFlowAnswerGenerator(llm_client)


def _build_vector_store(settings: Settings) -> VectorStore:
    """按配置取向量库后端:memory = 内存基线,chroma = Chroma 持久库(M3 第二层)。"""
    if settings.vector_store == "chroma":
        from app.repositories.vector_chroma import ChromaVectorStore  # 局部导入:默认路径零加载

        return ChromaVectorStore(persist_dir=settings.chroma_path)
    return InMemoryVectorStore()


@dataclass(slots=True)
class _Persistence:
    """装配产物:仓储 + 向量库 + 可选的共享连接池(pgvector 模式)。"""

    documents: DocumentRepository
    chunks: ChunkRepository
    query_logs: QueryLogRepository
    feedbacks: FeedbackRepository
    stats: StatsRepository
    store: VectorStore
    # pgvector 模式下四仓储与向量库共用;memory / chroma 模式恒为 None
    pg: PgDatabase | None = None


def _build_persistence(settings: Settings, vector_store: VectorStore | None) -> _Persistence:
    """按配置装配持久化层:M3 三级存储的最后一级(pgvector)接六张生产表。

    memory / chroma 模式:四个数据仓储保持进程内存实现(测试 / 无 Docker 开发);
    pgvector 模式:四仓储 + 向量库全部切到 PostgreSQL,共用一个连接池,
    构造即跑幂等建库(六张表),库不可达在此显式失败,绝不静默回退。

    统计仓储(T16)在四仓储之上做聚合,两种模式都随之切换:memory 形态随
    进程失忆,pgvector 形态读真库,重启不丢。
    """
    if settings.vector_store == "pgvector":
        from app.repositories.postgres import (
            PgChunkRepository,
            PgDatabase,
            PgDocumentRepository,
            PgFeedbackRepository,
            PgQueryLogRepository,
            PgStatsRepository,
        )
        from app.repositories.vector_pgvector import PgVectorStore

        pg_database = PgDatabase(settings.database_url)
        documents = PgDocumentRepository(pg_database)
        chunks = PgChunkRepository(pg_database)
        query_logs = PgQueryLogRepository(pg_database)
        return _Persistence(
            documents=documents,
            chunks=chunks,
            query_logs=query_logs,
            feedbacks=PgFeedbackRepository(pg_database),
            stats=PgStatsRepository(db=pg_database),
            store=vector_store if vector_store is not None else PgVectorStore(pg_database),
            pg=pg_database,
        )
    # memory / chroma:数据仓储零依赖进程内存实现(M2 / M4 基线)
    documents = InMemoryDocumentRepository()
    chunks = InMemoryChunkRepository()
    query_logs = InMemoryQueryLogRepository()
    return _Persistence(
        documents=documents,
        chunks=chunks,
        query_logs=query_logs,
        feedbacks=InMemoryFeedbackRepository(),
        stats=InMemoryStatsRepository(
            documents=documents, chunks=chunks, query_logs=query_logs
        ),
        store=vector_store if vector_store is not None else _build_vector_store(settings),
    )


def create_app(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
    answer_generator: AnswerGenerator | None = None,
    keyword_index: KeywordIndex | None = None,
    query_planner: QueryPlanner | None = None,
    reranker: Reranker | None = None,
    vector_store: VectorStore | None = None,
) -> FastAPI:
    """建应用;embedder、生成器与高级检索协作对象可注入替身(测试),缺省走真实实现。

    embedder 与文档仓储、切片仓储、向量库、关键词索引一起注入服务层;
    生成器在问答服务处替换。真实上游与假替身在主接缝处互换,其余装配零改动。
    """
    settings = settings or get_settings()
    configure_logging(settings)

    # 在线问答答档缓存(T15):相同问题在 TTL 内复用,知识库变化时整体失效
    answer_cache = (
        AnswerCache(
            ttl_seconds=settings.qa_cache_ttl_seconds,
            maxsize=settings.qa_cache_maxsize,
        )
        if settings.qa_cache_enabled
        else None
    )

    llm_client = UnifiedLLMClient(settings)
    effective_embedder = embedder if embedder is not None else llm_client
    effective_generator = (
        answer_generator
        if answer_generator is not None
        else _build_answer_generator(settings, llm_client)
    )
    persistence = _build_persistence(settings, vector_store)
    document_repository = persistence.documents
    chunk_repository = persistence.chunks
    effective_vector_store = persistence.store
    effective_keyword_index = keyword_index if keyword_index is not None else InMemoryKeywordIndex()
    effective_planner = (
        query_planner if query_planner is not None else SiliconFlowQueryPlanner(llm_client)
    )
    effective_reranker = reranker if reranker is not None else SiliconFlowReranker(llm_client)
    query_log_repository = persistence.query_logs
    feedback_repository = persistence.feedbacks
    feedback_service = FeedbackService(
        feedback_repository=feedback_repository,
        query_log_repository=query_log_repository,
    )
    document_storage = LocalDocumentStorage(settings.upload_dir)
    lifecycle_service = DocumentLifecycleService(
        document_repository=document_repository,
        chunk_repository=chunk_repository,
        vector_store=effective_vector_store,
        keyword_index=effective_keyword_index,
        storage=document_storage,
        answer_cache=answer_cache,
    )
    ingestion_service = IngestionService(
        repository=document_repository,
        storage=document_storage,
        lifecycle_service=lifecycle_service,
    )
    chunking_service = ChunkingService(
        document_repository=document_repository,
        chunk_repository=chunk_repository,
        settings=settings,
    )
    embedding_service = EmbeddingService(
        document_repository=document_repository,
        chunk_repository=chunk_repository,
        vector_store=effective_vector_store,
        embedder=effective_embedder,
        keyword_index=effective_keyword_index,
        answer_cache=answer_cache,
        batch_size=settings.embedding_batch_size,
    )
    ingestion_pipeline = IngestionPipeline(
        ingestion_service=ingestion_service,
        chunking_service=chunking_service,
        embedding_service=embedding_service,
    )
    retrieval_service = RetrievalService(
        vector_store=effective_vector_store,
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
        answer_cache=answer_cache,
    )
    # 运营统计与成本统计(T16):统计仓储在四仓储之上做聚合,服务层折算成本
    stats_service = StatsService(
        repository=persistence.stats,
        settings=settings,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.llm_client = llm_client
        try:
            yield
        finally:
            # pgvector 模式:连接池随应用关闭归还;memory / chroma 无此资源
            if persistence.pg is not None:
                persistence.pg.close()
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
    app.state.ingestion_pipeline = ingestion_pipeline
    app.state.retrieval_service = retrieval_service
    app.state.keyword_index = effective_keyword_index
    app.state.answer_service = answer_service
    app.state.answer_cache = answer_cache
    app.state.query_log_repository = query_log_repository
    app.state.feedback_service = feedback_service
    app.state.stats_service = stats_service
    app.state.vector_store = effective_vector_store
    app.state.lifecycle_service = lifecycle_service
    # pgvector 模式的共享连接池(memory / chroma 恒为 None),运维与测试清理用
    app.state.pg_database = persistence.pg

    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(chunks_router)
    app.include_router(embeddings_router)
    app.include_router(search_router)
    app.include_router(chat_router)
    app.include_router(query_logs_router)
    app.include_router(feedback_router)
    app.include_router(stats_router)
    return app
