"""仓储接口:领域层定义契约,基础设施层给实现(ADR-0001)。"""

from __future__ import annotations

from typing import Protocol

from app.domain.documents import DocumentRecord


class DocumentRepository(Protocol):
    """知识库文档档案的存取契约。

    M1 提供进程内存实现;M3 换 PostgreSQL(六张表之一 documents),契约不变。
    """

    def add(self, record: DocumentRecord) -> None:
        """登记新文档;同 id 重复登记视为程序错误。"""
        ...

    def get(self, document_id: str) -> DocumentRecord | None:
        """按 id 取档案,不存在返回 None。"""
        ...

    def save(self, record: DocumentRecord) -> None:
        """保存档案(状态机流转后的落库)。"""
        ...
