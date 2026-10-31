"""chunks 表仓储:整组替换 / 按文档取全部 / 连带删除,与内存基线同契约。

重切即换:``replace`` 在单个事务里删旧插新,任何时候不会出现半批切片;
按 ``chunk_index`` 排序还原文档的切片序。
"""

from __future__ import annotations

from collections.abc import Sequence

from psycopg.rows import dict_row

from app.domain.chunks import Chunk
from app.repositories.postgres.base import PgDatabase, placeholder_sql
from app.repositories.postgres.rows import chunk_from_row

_COLUMNS = (
    "chunk_id, document_id, chunk_index, content, tenant, department, doc_type, "
    "version, page_number, title, element_index_start, element_index_end"
)

_INSERT = f"INSERT INTO chunks ({_COLUMNS}) VALUES ({placeholder_sql(len(_COLUMNS.split(', ')))})"

_SELECT = f"SELECT {_COLUMNS} FROM chunks"


def _row_values(chunk: Chunk) -> tuple:
    return (
        chunk.chunk_id,
        chunk.document_id,
        chunk.index,
        chunk.content,
        chunk.tenant,
        chunk.department,
        chunk.doc_type,
        chunk.version,
        chunk.page_number,
        chunk.title,
        chunk.element_index_start,
        chunk.element_index_end,
    )


def _chunk_from_row(row: dict) -> Chunk:
    """行 → 切片:映射口径收口在 :mod:`app.repositories.postgres.rows`。"""
    return chunk_from_row(row)


class PgChunkRepository:
    """chunks 表的数据仓储;一个文档一组切片,重切即整组替换。"""

    def __init__(self, db: PgDatabase) -> None:
        self._db = db

    def replace(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        with self._db.connection() as conn, conn.cursor() as cursor, conn.transaction():
            cursor.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            cursor.executemany(_INSERT, [_row_values(chunk) for chunk in chunks])

    def list_by_document(self, document_id: str) -> list[Chunk]:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"{_SELECT} WHERE document_id = %s ORDER BY chunk_index ASC",
                (document_id,),
            )
            rows = cursor.fetchall()
        return [_chunk_from_row(row) for row in rows]

    def delete(self, document_id: str) -> None:
        with self._db.connection() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
