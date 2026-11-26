"""FastAPI 应用装配。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from fastapi import Depends, FastAPI

from app.api.dependencies import reject_tenant_parameter, require_principal
from app.api.routes.admin import alias_router as admin_alias_router
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
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
    AuditRepository,
    ChunkRepository,
    DocumentGrantRepository,
    DocumentRepository,
    FeedbackRepository,
    PrincipalRepository,
    QueryLogRepository,
    StatsRepository,
)
from app.domain.retrieval import (
    EmbeddingRef,
    KeywordIndex,
    QueryPlanner,
    VectorStore,
)
from app.integrations.llm.client import UnifiedLLMClient
from app.repositories.keyword_memory import InMemoryKeywordIndex
from app.repositories.memory import (
    InMemoryAuditRepository,
    InMemoryChunkRepository,
    InMemoryDocumentGrantRepository,
    InMemoryDocumentRepository,
    InMemoryFeedbackRepository,
    InMemoryPrincipalRepository,
    InMemoryQueryLogRepository,
    InMemoryStatsRepository,
)
from app.repositories.vector_memory import InMemoryVectorStore
from app.services.access import AccessService
from app.services.answering import AnswerService
from app.services.audit import AuditService
from app.services.auth import AuthService, TokenService
from app.services.chunking import ChunkingService
from app.services.document_catalog import DocumentCatalogService
from app.services.embedding import Embedder, EmbeddingService
from app.services.feedback import FeedbackService
from app.services.generation import (
    AnswerGenerator,
    RuleBasedAnswerGenerator,
    SiliconFlowAnswerGenerator,
)
from app.services.grants import DocumentGrantService
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

logger = structlog.get_logger("app")


def _embedding_ref_of(settings: Settings) -> EmbeddingRef:
    """当前配置声明的索引身份:写入与检索同源,只此一处构造(ADR-0019)。"""
    return EmbeddingRef(
        model=settings.embedding_model,
        version=settings.embedding_version,
        dimension=settings.embedding_dimension,
    )


def _warn_embedding_identity(store: VectorStore, current: EmbeddingRef) -> None:
    """启动期身份盘点:存在非当前身份 / 未知身份的向量时大声告警。

    不阻断启动:旧向量会被检索身份条件排除,表现为"查不到 / 拒答"而不是
    混算降分;运维据告警重嵌或跑回填脚本。盘点失败也不影响服务可用性。
    """
    try:
        counts = store.identity_counts()
    except Exception as exc:  # 启动期盘点失败不阻断服务
        logger.warning("embedding_identity_check_failed", error=str(exc))
        return
    stale = [
        {
            "model": ref.model if ref is not None else None,
            "version": ref.version if ref is not None else None,
            "dimension": ref.dimension if ref is not None else None,
            "count": count,
        }
        for ref, count in counts.items()
        if ref != current
    ]
    if not stale:
        return
    logger.warning(
        "embedding_identity_mismatch",
        current={
            "model": current.model,
            "version": current.version,
            "dimension": current.dimension,
        },
        stale=stale,
        stale_total=sum(item["count"] for item in stale),
    )


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
    principals: PrincipalRepository
    grants: DocumentGrantRepository
    audits: AuditRepository
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
            PgAuditRepository,
            PgChunkRepository,
            PgDatabase,
            PgDocumentGrantRepository,
            PgDocumentRepository,
            PgFeedbackRepository,
            PgPrincipalRepository,
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
            principals=PgPrincipalRepository(pg_database),
            grants=PgDocumentGrantRepository(pg_database),
            audits=PgAuditRepository(pg_database),
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
        # 不创建默认账号:内存模式下由调用方显式传入种子夹具(ADR-0020)
        principals=InMemoryPrincipalRepository(),
        grants=InMemoryDocumentGrantRepository(),
        audits=InMemoryAuditRepository(),
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
    principal_repository: PrincipalRepository | None = None,
    grant_repository: DocumentGrantRepository | None = None,
    audit_repository: AuditRepository | None = None,
) -> FastAPI:
    """建应用;embedder、生成器与高级检索协作对象可注入替身(测试),缺省走真实实现。

    embedder 与文档仓储、切片仓储、向量库、关键词索引一起注入服务层;
    生成器在问答服务处替换。真实上游与假替身在主接缝处互换,其余装配零改动。
    ``principal_repository`` 供测试注入内存种子夹具;服务启动从不自动建号。
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
    # 显式注入优先:测试用内存种子夹具;未注入时随持久化模式取主体仓储
    effective_principal_repository = (
        principal_repository if principal_repository is not None else persistence.principals
    )
    token_service = TokenService.from_settings(settings)
    auth_service = AuthService(
        principal_repository=effective_principal_repository,
        token_service=token_service,
    )
    document_repository = persistence.documents
    grant_repository = (
        grant_repository if grant_repository is not None else persistence.grants
    )
    audit_repository = (
        audit_repository if audit_repository is not None else persistence.audits
    )
    audit_service = AuditService(repository=audit_repository)
    # 授权范围与对象级可见性只此一处构造:路由只解析主体(ADR-0020 / 0021)
    access_service = AccessService(
        document_repository=document_repository,
        grant_repository=grant_repository,
    )
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
        grant_repository=grant_repository,
    )
    ingestion_service = IngestionService(
        repository=document_repository,
        storage=document_storage,
        lifecycle_service=lifecycle_service,
        access_service=access_service,
    )
    chunking_service = ChunkingService(
        document_repository=document_repository,
        chunk_repository=chunk_repository,
        settings=settings,
    )
    embedding_ref = _embedding_ref_of(settings)
    embedding_service = EmbeddingService(
        document_repository=document_repository,
        chunk_repository=chunk_repository,
        vector_store=effective_vector_store,
        embedder=effective_embedder,
        embedding=embedding_ref,
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
    # 知识库文档清单(T17):只读端点,让知识库能列举自己的文档(ADR-0015)
    document_catalog_service = DocumentCatalogService(
        repository=document_repository,
    )
    document_grant_service = DocumentGrantService(
        document_repository=document_repository,
        grant_repository=grant_repository,
        audit_service=audit_service,
        answer_cache=answer_cache,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.llm_client = llm_client
        _warn_embedding_identity(effective_vector_store, embedding_ref)
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
    app.state.principal_repository = effective_principal_repository
    app.state.token_service = token_service
    app.state.auth_service = auth_service
    app.state.access_service = access_service
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
    app.state.document_catalog_service = document_catalog_service
    app.state.document_grant_service = document_grant_service
    app.state.audit_service = audit_service
    app.state.vector_store = effective_vector_store
    app.state.lifecycle_service = lifecycle_service
    # pgvector 模式的共享连接池(memory / chroma 恒为 None),运维与测试清理用
    app.state.pg_database = persistence.pg

    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    # 公共面只有存活探针与登录;其余端点一律先过真实鉴权(ADR-0020)
    app.include_router(health_router)
    app.include_router(auth_router)
    # 管理端点:普通 tenant 参数不接受;平台管理员在依赖里显式选租户并留痕
    app.include_router(admin_router, dependencies=[Depends(require_principal)])
    app.include_router(admin_alias_router, dependencies=[Depends(require_principal)])
    protected_routers = (
        documents_router,
        chunks_router,
        embeddings_router,
        search_router,
        chat_router,
        query_logs_router,
        feedback_router,
        stats_router,
    )
    for router in protected_routers:
        app.include_router(
            router,
            dependencies=[Depends(require_principal), Depends(reject_tenant_parameter)],
        )
    return app
