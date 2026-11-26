"""授权范围服务:把已认证主体变成服务端强制的检索前范围(ADR-0020)。

路由只解析主体,不自己拼租户;所有授权范围的构造与对象级可见性判断
都收口在本服务,避免各路由散写后出现口径漂移。
"""

from __future__ import annotations

from app.core.errors import NotFoundError, ScopeViolationError
from app.domain.documents import DocumentRecord
from app.domain.identity import Principal, ScopeNarrowing
from app.domain.repositories import DocumentRepository
from app.domain.retrieval import Scope


class AccessService:
    """主体 → 授权范围;对象访问 → 单一口径 404。"""

    def __init__(self, *, document_repository: DocumentRepository) -> None:
        self._documents = document_repository

    def scope_for(self, principal: Principal, narrowing: ScopeNarrowing | None) -> Scope:
        """以主体租户为硬边界,叠加调用方可选的收窄条件。

        收窄到某份文档时,该文档必须存在且属于主体租户;越界返回 403,
        不做静默裁剪 —— 静默裁剪会让调用方误以为权限只是"没查到"。
        """
        fields = narrowing.model_dump(exclude_none=True) if narrowing else {}
        document_id = fields.get("document_id")
        if document_id is not None:
            record = self._documents.get(document_id)
            if record is None or record.tenant != principal.tenant:
                raise ScopeViolationError(
                    "收窄的文档不在授权范围内",
                    details={"document_id": document_id},
                )
        return Scope(tenant=principal.tenant, **fields)

    def require_document(self, *, scope: Scope, document_id: str) -> DocumentRecord:
        """对象级可见性的唯一入口:跨租户与不存在一律 404,不泄露存在性。"""
        record = self._documents.get(document_id)
        if record is None or record.tenant != scope.tenant:
            raise NotFoundError(
                "知识库文档不存在", details={"document_id": document_id}
            )
        return record
