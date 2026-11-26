"""生产表的幂等 DDL(T13,教材第 42 / 45 章生产表)。

documents / chunks / embeddings / query_logs / feedbacks / evaluation_runs,
外加身份底座的主体表 principals(ADR-0020);全部 ``IF NOT EXISTS``:
同一句法可重复执行,重启容器与重复建库不动既有数据。

设计要点(与内存基线的语义对齐见各仓储实现):

- ``embeddings`` 带**来源身份三列**(模型 / 版本 / 维度,可空):检索只与
  同一身份的向量互算分数,历史行在回填前不参与检索(ADR-0019);
  ``embedding`` 列仍不带维度修饰,维度正确性由写入侧断言保证,代价是走
  精确顺序扫描而不是 ANN 索引(见 vector_pgvector 模块说明);
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
        owner_id       text,
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
    # 已有库补列:历史文档没有所有者记录,保持 NULL(不伪造归属)
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS owner_id text",
    # 主体表(ADR-0020):服务启动不建号;种子脚本显式 upsert。username 只对
    # 用户主体有值(服务账号可为空),唯一约束保证登录名不歧义。
    """
    CREATE TABLE IF NOT EXISTS principals (
        principal_id text PRIMARY KEY,
        username     text UNIQUE,
        kind         text NOT NULL,
        tenant       text NOT NULL,
        role         text NOT NULL,
        department   text,
        display_name text,
        -- 服务账号只建模、不签发:允许没有口令哈希;用户账号由种子脚本写入
        password_hash text,
        created_at   timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS principals_by_tenant ON principals (tenant)",
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
        strategy            text,
        page_number         integer,
        title               text,
        element_index_start integer NOT NULL,
        element_index_end   integer NOT NULL
    )
    """,
    # 已有库补列:历史切片没有策略记录,保持 NULL,不做失真回填
    "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS strategy text",
    "CREATE INDEX IF NOT EXISTS chunks_by_document ON chunks (document_id)",
    """
    CREATE TABLE IF NOT EXISTS embeddings (
        chunk_id            text PRIMARY KEY,
        document_id         text NOT NULL,
        embedding           vector NOT NULL,
        embedding_model     text,
        embedding_version   text,
        embedding_dimension integer,
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
    # 来源身份三列:可空 = 历史行未知,回填前不参与检索(ADR-0019)。
    # 启动路径只做加法:不收紧列类型、不加 CHECK、不回填覆盖。
    "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS embedding_model text",
    "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS embedding_version text",
    "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS embedding_dimension integer",
    "CREATE INDEX IF NOT EXISTS embeddings_by_document ON embeddings (document_id)",
    """
    CREATE TABLE IF NOT EXISTS query_logs (
        seq                 bigserial NOT NULL,
        query_id            text PRIMARY KEY,
        trace_id            text NOT NULL,
        tenant              text NOT NULL DEFAULT 'default',
        principal_id        text NOT NULL DEFAULT 'system',
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
    # 已有库补列:存量日志落在种子租户 default,主体未知记为 system
    "ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS tenant text NOT NULL DEFAULT 'default'",
    "ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS principal_id text NOT NULL DEFAULT 'system'",
    "CREATE INDEX IF NOT EXISTS query_logs_by_tenant_principal "
    "ON query_logs (tenant, principal_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS feedbacks (
        seq         bigserial NOT NULL,
        feedback_id text PRIMARY KEY,
        query_id    text NOT NULL,
        trace_id    text NOT NULL,
        tenant      text NOT NULL DEFAULT 'default',
        principal_id text NOT NULL DEFAULT 'system',
        verdict     text NOT NULL,
        comment     text,
        created_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE feedbacks ADD COLUMN IF NOT EXISTS tenant text NOT NULL DEFAULT 'default'",
    "ALTER TABLE feedbacks ADD COLUMN IF NOT EXISTS principal_id text NOT NULL DEFAULT 'system'",
    "CREATE INDEX IF NOT EXISTS feedbacks_by_query ON feedbacks (query_id)",
    "CREATE INDEX IF NOT EXISTS feedbacks_by_verdict ON feedbacks (verdict)",
    "CREATE INDEX IF NOT EXISTS feedbacks_by_tenant_principal "
    "ON feedbacks (tenant, principal_id, created_at DESC)",
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
