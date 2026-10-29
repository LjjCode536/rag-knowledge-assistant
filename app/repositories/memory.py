"""文档与切片仓储的进程内存实现:M2 基线,M3 换 PostgreSQL(六张表)。"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from app.domain.chunks import Chunk
from app.domain.documents import DocumentRecord


class InMemoryDocumentRepository:
    """线程安全的内存仓储。

    上传端点运行在线程池中,读写必须加锁;
    重启即失忆,生产形态由 M3 的 PostgreSQL 接管。
    """

    def __init__(self) -> None:
        self._records: dict[str, DocumentRecord] = {}
        self._lock = threading.Lock()

    def add(self, record: DocumentRecord) -> None:
        with self._lock:
            if record.id in self._records:
                raise ValueError(f"文档已存在:{record.id}")
            self._records[record.id] = record

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._lock:
            return self._records.get(document_id)

    def save(self, record: DocumentRecord) -> None:
        with self._lock:
            self._records[record.id] = record


class InMemoryChunkRepository:
    """切片仓储的内存实现:一个文档一组切片,重切即整组替换。

    上传 / 切片端点运行在线程池中,读写必须加锁;生产形态由 M3 的 PostgreSQL 接管。
    """

    def __init__(self) -> None:
        self._chunks: dict[str, list[Chunk]] = {}
        self._lock = threading.Lock()

    def replace(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        with self._lock:
            self._chunks[document_id] = list(chunks)

    def list_by_document(self, document_id: str) -> list[Chunk]:
        with self._lock:
            return list(self._chunks.get(document_id, []))
