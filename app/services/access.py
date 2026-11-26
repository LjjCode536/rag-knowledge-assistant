"""授权范围服务:把已认证主体变成服务端强制的检索前范围(ADR-0020 / 0021)。

路由只解析主体,不自己拼租户;所有授权范围的构造与对象级可见性判断
都收口在本服务,避免各路由散写后出现口径漂移。

默认不可读:一份文档对主体可见,当且仅当同租户,且主体是租户 ``admin``、
是文档所有者,或存在一条生效的文档授权(用户 / 部门 / 整个租户)。授权解析
成允许的文档 id 集合,进入检索前过滤;**空集合表示一条都查不到**。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import NotFoundError, ScopeViolationError
from app.domain.audit import AuditAction
from app.domain.documents import DocumentRecord
from app.domain.identity import Principal, Role, ScopeNarrowing
from app.domain.repositories import DocumentGrantRepository, DocumentRepository
from app.domain.retrieval import Scope


@dataclass(frozen=True, slots=True)
class UploadAuthorization:
    """一次上传被解析后的归属:部门取主体默认或管理员显式指定,旧版本待退位。"""

    department: str | None
    previous: DocumentRecord | None


class AccessService:
    """主体 → 授权范围;对象访问 → 单一口径 404。"""

    def __init__(
        self,
        *,
        document_repository: DocumentRepository,
        grant_repository: DocumentGrantRepository | None = None,
    ) -> None:
        self._documents = document_repository
        # 默认空授权仓储:只保留所有者 / 管理员可见;生产装配传入真实实现
        self._grants = grant_repository

    # ===== 主体 → 允许集合 =====

    def allowed_document_ids(
        self, principal: Principal, *, tenant: str | None = None
    ) -> frozenset[str]:
        """解析主体在租户内可读的文档 id 集合(授权核心,单一实现)。

        - 租户 ``admin``:本租户全部文档(含退位 / 删除档案,供审计与历史查询);
        - 其他主体:自己所有的文档(含历史版本)+ 授权逻辑文档展开出的
          现行版本。授权按 (租户, 文件名) 挂载,版本退位后仍生效。
        """
        tenant = tenant or principal.tenant
        if principal.role is Role.ADMIN:
            return frozenset(self._documents.list_ids(tenant=tenant, latest_only=False))

        owned = self._documents.list_ids(
            tenant=tenant, owner_id=principal.id, latest_only=False
        )
        granted_ids: set[str] = set()
        if self._grants is not None:
            relevant = self._grants.list_relevant(
                tenant=tenant,
                principal_id=principal.id,
                department=principal.department,
            )
            # 租户级授权仍挂在该逻辑文档上("发布到租户"),不是全库放行
            file_names = {grant.file_name for grant in relevant}
            if file_names:
                granted_ids.update(
                    self._documents.list_ids(
                        tenant=tenant,
                        file_names=sorted(file_names),
                        latest_only=True,
                    )
                )
        return frozenset(set(owned) | granted_ids)

    def scope_for(
        self,
        principal: Principal,
        narrowing: ScopeNarrowing | None,
        *,
        tenant: str | None = None,
    ) -> Scope:
        """以主体租户为硬边界,叠加调用方可选的收窄条件。

        收窄到某份文档时,该文档必须存在且属于主体可读集合;越界返回 403,
        不做静默裁剪 —— 静默裁剪会让调用方误以为权限只是"没查到"。
        """
        fields = narrowing.model_dump(exclude_none=True) if narrowing else {}
        document_id = fields.get("document_id")
        allowed = self.allowed_document_ids(principal, tenant=tenant)
        if document_id is not None:
            record = self._documents.get(document_id)
            if (
                record is None
                or record.tenant != (tenant or principal.tenant)
                or document_id not in allowed
            ):
                raise ScopeViolationError(
                    "收窄的文档不在授权范围内",
                    details={"document_id": document_id},
                    audit={
                        "action": AuditAction.ACCESS_DENIED,
                        "resource_type": "document",
                        "resource_id": document_id,
                        "reason": "document_not_readable",
                    },
                )
            allowed = frozenset({document_id})
        return Scope(
            tenant=tenant or principal.tenant,
            document_ids=allowed,
            **fields,
        )

    def require_document(self, *, scope: Scope, document_id: str) -> DocumentRecord:
        """对象级可见性的唯一入口:跨租户与不存在一律 404,不泄露存在性。"""
        record = self._documents.get(document_id)
        if record is None:
            raise NotFoundError(
                "知识库文档不存在",
                details={"document_id": document_id},
                audit={
                    "action": AuditAction.ACCESS_DENIED,
                    "resource_type": "document",
                    "resource_id": document_id,
                    "reason": "not_found",
                },
            )
        if record.tenant != scope.tenant:
            raise NotFoundError(
                "知识库文档不存在",
                details={"document_id": document_id},
                audit={
                    "action": AuditAction.ACCESS_CROSS_TENANT,
                    "resource_type": "document",
                    "resource_id": document_id,
                    "reason": "cross_tenant",
                    "resource_tenant": record.tenant,
                },
            )
        if (
            scope.document_ids is not None
            and record.id not in scope.document_ids
        ):
            raise NotFoundError(
                "知识库文档不存在",
                details={"document_id": document_id},
                audit={
                    "action": AuditAction.ACCESS_DENIED,
                    "resource_type": "document",
                    "resource_id": document_id,
                    "reason": "not_readable",
                },
            )
        return record

    # ===== 写路径 =====

    def authorize_upload(
        self,
        *,
        principal: Principal,
        file_name: str,
        department: str | None,
    ) -> UploadAuthorization:
        """上传写路径的策略收口:租户固定取主体,部门与同名覆盖按角色判定。

        - 部门缺省取主体部门;只有 ``admin`` 能显式指定其他部门,成员越界
          返回 403 ``SCOPE_VIOLATION``(不静默改成自己的部门);
        - 同名文档已存在时,成员只能重传自己所有的文档,否则按对象不可见
          返回 404;管理员可重传本租户任意文档。所有者的既有归属不因
          重传而改变。
        """
        requested = _blank_to_none(department)
        if (
            requested is not None
            and requested != principal.department
            and principal.role is not Role.ADMIN
        ):
            raise ScopeViolationError(
                "上传部门超出主体授权范围",
                details={"department": requested},
                audit={
                    "action": AuditAction.ACCESS_DENIED,
                    "resource_type": "upload",
                    "resource_id": file_name,
                    "reason": "department_out_of_scope",
                },
            )
        previous = self._documents.find_latest_by_file(
            tenant=principal.tenant, file_name=file_name
        )
        if (
            previous is not None
            and previous.owner_id != principal.id
            and principal.role is not Role.ADMIN
        ):
            raise NotFoundError(
                "知识库文档不存在",
                details={"file_name": file_name},
                audit={
                    "action": AuditAction.ACCESS_DENIED,
                    "resource_type": "document",
                    "resource_id": previous.id,
                    "reason": "reupload_not_owner",
                },
            )
        return UploadAuthorization(
            department=requested or principal.department,
            previous=previous,
        )

    def require_writable_document(
        self, *, principal: Principal, document_id: str
    ) -> DocumentRecord:
        """写路径对象级入口:切片 / 向量化 / 删除只允许所有者或租户管理员。

        跨租户、非所有者成员与不存在的对象共用同一个 404,不泄露存在性;
        管理员可操作本租户全部文档。
        """
        record = self._documents.get(document_id)
        if record is None:
            raise NotFoundError(
                "知识库文档不存在",
                details={"document_id": document_id},
                audit={
                    "action": AuditAction.ACCESS_DENIED,
                    "resource_type": "document",
                    "resource_id": document_id,
                    "reason": "not_found",
                },
            )
        if record.tenant != principal.tenant:
            raise NotFoundError(
                "知识库文档不存在",
                details={"document_id": document_id},
                audit={
                    "action": AuditAction.ACCESS_CROSS_TENANT,
                    "resource_type": "document",
                    "resource_id": document_id,
                    "reason": "cross_tenant",
                    "resource_tenant": record.tenant,
                },
            )
        if principal.role is not Role.ADMIN and record.owner_id != principal.id:
            raise NotFoundError(
                "知识库文档不存在",
                details={"document_id": document_id},
                audit={
                    "action": AuditAction.ACCESS_DENIED,
                    "resource_type": "document",
                    "resource_id": document_id,
                    "reason": "not_owner",
                },
            )
        return record


def _blank_to_none(value: str | None) -> str | None:
    """空白串按未提供处理,避免把空表单值写成空部门。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
