"""FastAPI 应用装配。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.chunks import router as chunks_router
from app.api.routes.documents import router as documents_router
from app.api.routes.health import router as health_router
from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.tracing import TraceIdMiddleware
from app.integrations.llm.client import UnifiedLLMClient
from app.repositories.memory import InMemoryChunkRepository, InMemoryDocumentRepository
from app.services.chunking import ChunkingService
from app.services.ingestion import IngestionService
from app.services.storage import LocalDocumentStorage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    llm_client = UnifiedLLMClient(settings)
    document_repository = InMemoryDocumentRepository()
    chunk_repository = InMemoryChunkRepository()
    ingestion_service = IngestionService(
        repository=document_repository,
        storage=LocalDocumentStorage(settings.upload_dir),
    )
    chunking_service = ChunkingService(
        document_repository=document_repository,
        chunk_repository=chunk_repository,
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

    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(chunks_router)
    return app
