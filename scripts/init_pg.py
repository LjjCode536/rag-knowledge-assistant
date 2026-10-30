"""建表 / 迁移脚本:对 DATABASE_URL 指向的库重复执行幂等 DDL(T13)。

六张生产表(documents / chunks / embeddings / query_logs / feedbacks /
evaluation_runs)与 pgvector 扩展全部 ``IF NOT EXISTS``:连续执行多次安全,
不动既有数据;应用启动(create_app,pgvector 模式)走同一入口。

用法::

    docker compose up -d db
    uv run python scripts/init_pg.py                       # 用应用配置 DATABASE_URL
    uv run python scripts/init_pg.py --database-url postgresql://rag:rag@127.0.0.1:5432/rag
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.repositories.postgres import PgDatabase
from app.repositories.postgres.schema import ensure_schema

_TABLES = (
    "documents",
    "chunks",
    "embeddings",
    "query_logs",
    "feedbacks",
    "evaluation_runs",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="幂等建表 / 迁移(pgvector 生产持久化)")
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL 连接串;缺省用应用配置 DATABASE_URL "
        "(.env 或环境变量,默认指向 compose:postgresql://rag:rag@127.0.0.1:5432/rag)",
    )
    args = parser.parse_args(argv)

    dsn = args.database_url or get_settings().database_url
    if not dsn or not dsn.strip():
        print(
            "未配置连接串:显式 --database-url 或设置 DATABASE_URL"
            "(docker compose up -d db 拉起本地库后复 .env.example)",
            file=sys.stderr,
        )
        return 2

    try:
        db = PgDatabase(dsn)  # 构造即第一次幂等建库;下面再重复执行一次验证幂等
        with db.connection() as conn:
            ensure_schema(conn)  # 第二次执行:重复迁移必须安全(验收口径)
            rows = db_counts(conn)
    except Exception as exc:
        print(f"建库失败:{exc}", file=sys.stderr)
        print("排查:容器是否在跑(db)?d)docker compose ps;连接串是否一致?", file=sys.stderr)
        return 1
    db.close()

    counts = " | ".join(f"{table}={count}" for table, count in rows)
    print("六张表全部就绪(幂等迁移已重复执行验证):", counts)
    return 0


def db_counts(conn) -> list[tuple[str, int]]:
    """六张表当前行数(建库摘要输出,供运维确认连接的确实是目标库)。"""
    counts: list[tuple[str, int]] = []
    for table in _TABLES:
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        counts.append((table, int(row[0])))
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
