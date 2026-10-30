"""PostgreSQL 连接核心:pgvector 模式(生产持久化)的仓储与向量库共用一把连接钥匙。

psycopg3 连接池 + 幂等建表(:mod:`app.repositories.postgres.schema`);
所有仓储实现共享本类:

- 连接均 autocommit:仓储的每次调用自成一个事务,失败不留半截状态,
  与内存基线"单操作即单事务"的语义一致;多语句原子替换由调用处在
  ``conn.transaction()`` 里显式包事务;
- 构造即建表(幂等):应用装配与迁移脚本走同一入口,重复启动安全;
- PostgreSQL 不可达时上抛 :class:`PgUnavailableError`(启动期明确报错,
  绝不静默回退内存态 —— "生产持久化"不得悄悄降级)。
"""

from __future__ import annotations

from psycopg_pool import ConnectionPool, PoolTimeout

from app.repositories.postgres.schema import ensure_schema

_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
_DEFAULT_READY_TIMEOUT_SECONDS = 10.0


class PgUnavailableError(RuntimeError):
    """PostgreSQL 不可达 / 未就绪:pgvector 模式必须显式失败,不静默回退。"""


def placeholder_sql(count: int) -> str:
    """N 列 VALUES 的占位串 ``%s, %s, ...``;占位个数与列名清单同源推导。"""
    return ", ".join(["%s"] * count)


class PgDatabase:
    """连接池持有者:构造时建库(pgvector 扩展 + 六张表),仓储借连接用完即还。"""

    def __init__(
        self,
        database_url: str,
        *,
        ready_timeout: float = _DEFAULT_READY_TIMEOUT_SECONDS,
    ) -> None:
        self.database_url = database_url
        self._pool = ConnectionPool(
            database_url,
            min_size=1,
            max_size=4,
            # autocommit:与内存基线同语义 —— 一次仓储调用一次落定,不留半截状态
            kwargs={"autocommit": True},
            open=False,
        )
        self._pool.open()
        try:
            self._pool.wait(timeout=ready_timeout)
        except PoolTimeout as exc:
            self._pool.close()
            raise PgUnavailableError(
                f"PostgreSQL 不可达(连接串指向的库未在 "
                f"{ready_timeout:.0f}s 内就绪):{database_url.rsplit('@', 1)[-1]}"
            ) from exc
        with self.connection() as conn:
            try:
                ensure_schema(conn)
            except Exception:
                # 建库失败即不可用:跳过归还池的连接,直接关闭,不留悬挂资源
                self._pool.close()
                raise

    def connection(self):
        """借一个连接;with 退出即归还池化连接(异常路径同样归还)。"""
        return self._pool.connection()

    def close(self) -> None:
        """归还并关闭全部连接(应用关闭 / 测试清理钩子)。"""
        self._pool.close()
