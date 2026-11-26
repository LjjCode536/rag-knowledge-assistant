"""知识库文档清单的用例层(T17):列出 + 计数,口径收口在此。

清单端点是"前端不改后端"边界的一处显式例外(ADR-0015):知识库服务必须
能列举自己的文档,而 `/stats` 已在做同一张表的计数 —— 列出是同一套过滤
条件的自然延伸。

本层负责:

- 把路由层的查询参数装配成领域层的 :class:`DocumentFilter`;
- 用主体构造的授权范围强制收敛租户,没有"不传即查全库"的兜底;
- 归一"空值即未提供"(空白串按未提供处理),使过滤条件语义唯一;
- 用**同一份筛选条件**分别取清单与总数,故 ``total`` 与清单口径必然一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.documents import DocumentFilter, DocumentRecord, DocumentStatus
from app.domain.repositories import DocumentRepository
from app.domain.retrieval import Scope


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """知识库文档清单的一页:总数 + 当前页条数。"""

    total: int
    limit: int
    offset: int
    items: list[DocumentRecord]


class DocumentCatalogService:
    """知识库文档清单服务:按筛选条件列出档案并给出总数。"""

    def __init__(self, *, repository: DocumentRepository) -> None:
        self._repository = repository

    def list_documents(
        self,
        *,
        scope: Scope,
        status: DocumentStatus | None = None,
        keyword: str | None = None,
        department: str | None = None,
        doc_type: str | None = None,
        latest_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> DocumentPage:
        """列出授权范围内的知识库文档并给出符合条件总数。

        租户由主体构造的 :class:`Scope` 强制收敛,没有"不传即查全库"的
        兜底;调用方只能在同一租户内继续收窄部门 / 文档类型等条件。

        默认只列现行版本(``is_latest = true``):退位的旧版本与已删除的
        文档不进默认清单,给 ``latest_only=False`` 可查历史。

        排序固定为创建时间倒序(最新在前);创建时间相同时按 id 兜底,
        次序确定,不依赖存储实现的偶然顺序。

        分页越界(偏移超出总数)返回空清单与正确总数,不报错。
        """
        filters = DocumentFilter(
            tenant=scope.tenant,
            status=status,
            keyword=_blank_to_none(keyword),
            department=_blank_to_none(department),
            doc_type=_blank_to_none(doc_type),
            latest_only=latest_only,
            limit=limit,
            offset=offset,
        )
        items = self._repository.list(filters)
        return DocumentPage(
            total=self._repository.count(filters),
            limit=filters.limit,
            offset=filters.offset,
            items=items,
        )


def _blank_to_none(value: str | None) -> str | None:
    """空白串按"未提供"处理。

    过滤条件里的空串与"没传"是同一件事(两者都不构成约束);归一在此层
    收口,内存实现与 PostgreSQL 实现因此看到同一份条件。
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
