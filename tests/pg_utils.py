"""PostgreSQL 集成测试的落地支持:按需连接测试库,不可用则整批跳过。

约定:`docker compose up -d db` 拉起的本地 Postgres(pgvector 镜像)是默认测试后端;
可用 ``RAG_TEST_PG_DSN`` 指向任意可达的 Postgres。连接失败时 pytest.skip,
保证无 Docker 环境 ``uv run pytest`` 依旧全绿(AGENTS.md:测试不触网、不依赖外部服务)。

每个用例使用独立的 ``rag_test`` 数据库(先整库重建再建表),仓储 / 向量库 / 迁移
幂等语义都从真库上验证,与生产同一连接形态。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg

DSN_ENV = "RAG_TEST_PG_DSN"
DEFAULT_DSN = "postgresql://rag:rag@127.0.0.1:5432/rag"
TEST_DATABASE_NAME = "rag_test"


@dataclass
class PgTestDatabase:
    """一个用例隔离的测试库:dsn 指向每用例新建的 rag_test 库。"""

    dsn: str


def admin_dsn() -> str:
    return os.environ.get(DSN_ENV, DEFAULT_DSN)


def _database_dsn(dsn: str, database: str) -> str:
    head = dsn.rsplit("/", 1)[0]
    return f"{head}/{database}"


def pg_available() -> bool:
    """默认测试后端是否可达:仅作跳过判定,不做任何写操作。"""
    try:
        psycopg.connect(admin_dsn(), connect_timeout=3).close()
    except psycopg.OperationalError:
        return False
    return True


def fresh_test_database() -> PgTestDatabase:
    """整库重建 rag_test(先 DROP ... FORCE 再 CREATE),给用例一张真正的白纸。"""
    dsn = admin_dsn()
    with psycopg.connect(
        dsn, connect_timeout=3, autocommit=True
    ) as conn, conn.cursor() as cursor:
        cursor.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE_NAME}" WITH (FORCE)')
        cursor.execute(f'CREATE DATABASE "{TEST_DATABASE_NAME}"')
    return PgTestDatabase(dsn=_database_dsn(dsn, TEST_DATABASE_NAME))
