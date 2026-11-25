"""回填向量来源身份(ADR-0019):把历史行的 embedding_model / version / dimension 补齐。

背景:identity 三列是后加的,历史向量没有来源信息;检索只认同一身份的行,
所以未回填的旧向量在检索里"消失"。本脚本按**实际向量维度**回填,不靠猜:

- 只处理三列同时为 NULL 的行;
- 只回填 ``vector_dims(embedding)`` 与目标维度一致的行;
- 维度不一致的行保持 NULL 并单独报告 —— 它们必须整篇重嵌,不能被错误标注。

默认 dry-run(只打印分布);确认后用 ``--apply`` 落库。

用法::

    uv run python scripts/backfill_embedding_identity.py
    uv run python scripts/backfill_embedding_identity.py --apply
    uv run python scripts/backfill_embedding_identity.py \\
        --model BAAI/bge-m3 --version v1 --dimension 1024 --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.repositories.postgres import PgDatabase

_NULL_IDENTITY = (
    "embedding_model IS NULL AND embedding_version IS NULL AND embedding_dimension IS NULL"
)


def _distribution(conn) -> list[tuple[int, int]]:
    """未回填行按实际向量维度分布:[(dimension, count), ...]。"""
    rows = conn.execute(
        "SELECT vector_dims(embedding) AS dim, count(*) "
        f"FROM embeddings WHERE {_NULL_IDENTITY} GROUP BY 1 ORDER BY 1"
    ).fetchall()
    return [(int(dim), int(count)) for dim, count in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="回填历史向量的来源身份(模型 / 版本 / 维度),默认 dry-run"
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL 连接串;缺省用应用配置 DATABASE_URL",
    )
    parser.add_argument("--model", default=None, help="缺省用配置 EMBEDDING_MODEL")
    parser.add_argument("--version", default=None, help="缺省用配置 EMBEDDING_VERSION")
    parser.add_argument(
        "--dimension", type=int, default=None, help="缺省用配置 EMBEDDING_DIMENSION"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写入;不带此开关只打印将回填的维度分布",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    dsn = args.database_url or settings.database_url
    if not dsn or not dsn.strip():
        print(
            "未配置连接串:显式 --database-url 或设置 DATABASE_URL",
            file=sys.stderr,
        )
        return 2

    model = args.model or settings.embedding_model
    version = args.version or settings.embedding_version
    dimension = args.dimension if args.dimension is not None else settings.embedding_dimension

    try:
        db = PgDatabase(dsn)
    except Exception as exc:
        print(f"连接 / 建库失败:{exc}", file=sys.stderr)
        return 1

    target = (model, version, dimension)
    try:
        with db.connection() as conn:
            distribution = _distribution(conn)
            if not distribution:
                print("没有待回填的历史向量(身份三列均为 NULL 的行数为 0)。")
                return 0

            print(f"目标身份:model={model} version={version} dimension={dimension}")
            print("未回填行的实际向量维度分布:")
            for dim, count in distribution:
                mark = "可回填" if dim == dimension else "跳过(需整篇重嵌)"
                print(f"  dimension={dim} count={count} -> {mark}")

            if not args.apply:
                print("\ndry-run:未写入。确认后加 --apply。")
                return 0

            cursor = conn.execute(
                "UPDATE embeddings SET embedding_model = %s, embedding_version = %s, "
                "embedding_dimension = %s "
                f"WHERE {_NULL_IDENTITY} AND vector_dims(embedding) = %s",
                (*target, dimension),
            )
            updated = cursor.rowcount
            remaining = sum(count for dim, count in _distribution(conn))
            print(f"\n已回填 {updated} 行;仍待处理 {remaining} 行(维度不符,需整篇重嵌)。")
    except Exception as exc:
        print(f"回填失败:{exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
