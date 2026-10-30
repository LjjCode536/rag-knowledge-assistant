"""生产持久化:PostgreSQL 16 + pgvector(M3 第三层,T13)。

六张生产表(documents / chunks / embeddings / query_logs / feedbacks /
evaluation_runs)的幂等 DDL 与四个数据仓储 + 评估留档仓储;
向量库的 pgvector 实现在 :mod:`app.repositories.vector_pgvector`。

装配(:meth:`app.main.create_app` 走同一分支,服务层零改动)::

    db = PgDatabase(settings.database_url)
    document_repository = PgDocumentRepository(db)
    chunk_repository = PgChunkRepository(db)
    query_log_repository = PgQueryLogRepository(db)
    feedback_repository = PgFeedbackRepository(db)
    vector_store = PgVectorStore(db)

契约与内存基线逐一对应;各实现共享同一把连接池:连接借出即还。
"""

from __future__ import annotations

from app.repositories.postgres.base import PgDatabase, PgUnavailableError
from app.repositories.postgres.chunks import PgChunkRepository
from app.repositories.postgres.documents import PgDocumentRepository
from app.repositories.postgres.evaluation_runs import PgEvaluationRunRepository
from app.repositories.postgres.feedbacks import PgFeedbackRepository
from app.repositories.postgres.query_logs import PgQueryLogRepository

__all__ = (
    "PgChunkRepository",
    "PgDatabase",
    "PgDocumentRepository",
    "PgEvaluationRunRepository",
    "PgFeedbackRepository",
    "PgQueryLogRepository",
    "PgUnavailableError",
)
