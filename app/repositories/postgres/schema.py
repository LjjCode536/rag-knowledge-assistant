"""六张生产表的幂等 DDL(T13,教材第 42 / 45 章生产表)。

documents / chunks / embeddings / query_logs / feedbacks / evaluation_runs,
全部 ``IF NOT EXISTS``:同一句法可重复执行,重启容器与重复建库不动既有数据。

设计要点(与内存基线的语义对齐见各仓储实现):

- ``embeddings.embedding`` 用**不带维度修饰**的 vector 列:换嵌入模型的
  迁移期允许新旧维度共存,维度不一致按 0 分降级(不崩溃),与内存基线同
  语义;代价是走精确顺序扫描而不是 ANN 索引(见 vector_pgvector 模块说明);
- ``embeddings`` 是**自带切片元数据快照**的自足存储(与 Chroma 的 metadata
  形态一致):重切作废旧切片后、重嵌入完成前,向量通道仍按旧快照检索 ——
  与内存基线 / Chroma 同语义(向量的生死只由 vector store 的 replace /
  delete 决定);chunks 表则是文档域的规范切片记录,快照在 replace 时写入;
- 时序排序列用 ``seq bigserial`` 兜底:created_at 同微秒时仍能给出稳定的
  插入序 ``(ORDER BY created_at DESC, seq DESC)``。
"""

from __future__ import annotations

import psycopg

SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS documents (
        id             text PRIMARY KEY,
        file_name      text NOT NULL,
        file_type      text NOT NULL,
        size_bytes     bigint NOT NULL,
        status         text NOT NULL,
        error_message  text,
        is_latest      boolean NOT NULL DEFAULT true,
        superseded_at  timestamptz,
        deleted_at     timestamptz,
        tenant         text NOT NULL DEFAULT 'default',
        department     text,
        doc_type       text,
        version        text NOT NULL DEFAULT 'v1',
        page_count     integer,
        element_count  integer,
        parsed         jsonb,
        chunk_count    integer,
        chunk_warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
        created_at     timestamptz NOT NULL DEFAULT now(),
        parsed_at      timestamptz,
        chunked_at     timestamptz,
        indexed_at     timestamptz,
        failed_at      timestamptz,
        seq            bigserial NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS documents_latest_by_file "
    "ON documents (tenant, file_name) WHERE is_latest",
    """
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id            text PRIMARY KEY,
        document_id         text NOT NULL,
        chunk_index         integer NOT NULL,
        content             text NOT NULL,
        tenant              text NOT NULL,
        department          text,
        doc_type            text,
        version             text NOT NULL,
        page_number         integer,
        title               text,
        element_index_start integer NOT NULL,
        element_index_end   integer NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS chunks_by_document ON chunks (document_id)",
    """
    CREATE TABLE IF NOT EXISTS embeddings (
        chunk_id            text PRIMARY KEY,
        document_id         text NOT NULL,
        embedding           vector NOT NULL,
        chunk_index         integer NOT NULL,
        content             text NOT NULL,
        tenant              text NOT NULL,
        department          text,
        doc_type            text,
        version             text NOT NULL,
        page_number         integer,
        title               text,
        element_index_start integer NOT NULL,
        element_index_end   integer NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS embeddings_by_document ON embeddings (document_id)",
    """
    CREATE TABLE IF NOT EXISTS query_logs (
        seq                 bigserial NOT NULL,
        query_id            text PRIMARY KEY,
        trace_id            text NOT NULL,
        question            text NOT NULL,
        answer              text NOT NULL,
        refused             boolean NOT NULL,
        refusal_reason      text,
        hits                jsonb NOT NULL DEFAULT '[]'::jsonb,
        filters             jsonb NOT NULL DEFAULT '{}'::jsonb,
        top_k               integer NOT NULL,
        min_score           double precision NOT NULL,
        latency_ms          double precision NOT NULL,
        generator           text NOT NULL,
        token_usage         jsonb NOT NULL DEFAULT '{}'::jsonb,
        rewritten_question  text,
        expanded_questions  jsonb NOT NULL DEFAULT '[]'::jsonb,
        created_at          timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS query_logs_recent ON query_logs (created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS feedbacks (
        seq         bigserial NOT NULL,
        feedback_id text PRIMARY KEY,
        query_id    text NOT NULL,
        trace_id    text NOT NULL,
        verdict     text NOT NULL,
        comment     text,
        created_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS feedbacks_by_query ON feedbacks (query_id)",
    "CREATE INDEX IF NOT EXISTS feedbacks_by_verdict ON feedbacks (verdict)",
    """
    CREATE TABLE IF NOT EXISTS evaluation_runs (
        run_id          text PRIMARY KEY,
        run_name        text NOT NULL,
        dataset_name    text NOT NULL,
        dataset_version integer NOT NULL,
        embedder        text NOT NULL,
        mode            text NOT NULL,
        vector_store    text,
        command         text NOT NULL,
        summary         jsonb NOT NULL,
        report          jsonb NOT NULL,
        created_at      timestamptz NOT NULL DEFAULT now(),
        seq             bigserial NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS evaluation_runs_recent ON evaluation_runs (created_at DESC)",
)


def ensure_schema(conn: psycopg.Connection) -> None:
    """在单个连接上执行全部幂等 DDL:重复调用安全,不动既有数据。"""
    with conn.transaction():
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
