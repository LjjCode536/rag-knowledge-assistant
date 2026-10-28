"""知识库文档状态机:唯一合法迁移表。

非法迁移视为领域不变量被破坏(程序错误),与业务失败(failed 状态)区分开。
"""

from __future__ import annotations

from app.domain.documents import DocumentStatus


class StateTransitionError(Exception):
    """状态迁移不合法。"""


_ALLOWED: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.UPLOADED: frozenset({DocumentStatus.PARSING}),
    DocumentStatus.PARSING: frozenset({DocumentStatus.PARSED, DocumentStatus.FAILED}),
    # 以下迁移由后续里程碑接入(M2 切分 / M3 向量化与入库),先固定契约:
    DocumentStatus.PARSED: frozenset({DocumentStatus.CHUNKING, DocumentStatus.FAILED}),
    DocumentStatus.CHUNKING: frozenset({DocumentStatus.EMBEDDING, DocumentStatus.FAILED}),
    DocumentStatus.EMBEDDING: frozenset({DocumentStatus.INDEXED, DocumentStatus.FAILED}),
    DocumentStatus.INDEXED: frozenset(),
    DocumentStatus.FAILED: frozenset(),
}


def ensure_transition(current: DocumentStatus, target: DocumentStatus) -> None:
    """校验迁移合法性,不合法时抛 :class:`StateTransitionError`。"""
    if target not in _ALLOWED[current]:
        raise StateTransitionError(f"文档状态不允许从 {current} 迁移到 {target}")
