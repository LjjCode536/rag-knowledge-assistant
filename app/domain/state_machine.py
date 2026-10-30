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
    # M2 切片接入;chunking 完成落 chunked 落点、重切允许 chunked → chunking(见 ADR-0002)
    DocumentStatus.PARSED: frozenset({DocumentStatus.CHUNKING, DocumentStatus.FAILED}),
    DocumentStatus.CHUNKING: frozenset({DocumentStatus.CHUNKED, DocumentStatus.FAILED}),
    # M3 向量化接入:向量化完成落 indexed 落点;重切仍走 chunked → chunking
    DocumentStatus.CHUNKED: frozenset(
        {DocumentStatus.CHUNKING, DocumentStatus.EMBEDDING, DocumentStatus.FAILED}
    ),
    # M7 异步 ingestion 将使用 embedding 瞬时态(见 ADR-0003)
    DocumentStatus.EMBEDDING: frozenset({DocumentStatus.INDEXED, DocumentStatus.FAILED}),
    DocumentStatus.INDEXED: frozenset(),
    DocumentStatus.FAILED: frozenset(),
    # T10 文档生命周期新增终态(见 ADR-0009):常规管线下无出边
    DocumentStatus.SUPERSEDED: frozenset(),
    DocumentStatus.DELETED: frozenset(),
}

# 退位与删除是跨状态的终结迁移:旧版本可从任意进行中 / 已入库 / 已失败
# 状态退位或被删除,不再逐状态枚举(ADR-0009)。
_LIFECYCLE_TARGETS = frozenset({DocumentStatus.SUPERSEDED, DocumentStatus.DELETED})


def ensure_transition(current: DocumentStatus, target: DocumentStatus) -> None:
    """校验迁移合法性,不合法时抛 :class:`StateTransitionError`。"""
    # 生命周期终态可从任意状态到达,包含同态迁移(并发退位 / 重复删除幂等)
    if target in _LIFECYCLE_TARGETS:
        return
    if target not in _ALLOWED[current]:
        raise StateTransitionError(f"文档状态不允许从 {current} 迁移到 {target}")
