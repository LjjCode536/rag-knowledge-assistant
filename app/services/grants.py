"""文档授权用例:所有者 / 管理员查看、添加、移除授权(ADR-0021)。

权限判断与审计收口在本服务:授权变更属于变更类操作,审计失败即不执行;
变更成功后整体失效答档缓存,确保撤销授权立即不再被检索 / 引用。
"""

from __future__ import annotations

from app.core.errors import NotFoundError, ScopeViolationError
from app.domain.audit import AuditAction
from app.domain.documents import DocumentRecord
from app.domain.grants import DocumentGrant, GrantSubjectType
from app.domain.identity import Principal, Role
from app.domain.repositories import DocumentGrantRepository, DocumentRepository
from app.services.audit import AuditService
from app.services.qa_cache import AnswerCache


class DocumentGrantService:
    """文档授权的管理入口:谁可见、谁能授予、授权后何时失效。"""

    def __init__(
        self,
        *,
        document_repository: DocumentRepository,
        grant_repository: DocumentGrantRepository,
        audit_service: AuditService,
        answer_cache: AnswerCache | None = None,
    ) -> None:
        self._documents = document_repository
        self._grants = grant_repository
        self._audit = audit_service
        self._answer_cache = answer_cache

    def list_grants(
        self, *, principal: Principal, document_id: str
    ) -> list[DocumentGrant]:
        """列出某文档的授权清单;只有所有者与租户管理员可查看。"""
        record = self._require_manager(principal=principal, document_id=document_id)
        return self._grants.list_for_logical(
            tenant=record.tenant, file_name=record.file_name
        )

    def add_grant(
        self,
        *,
        principal: Principal,
        document_id: str,
        subject_type: GrantSubjectType,
        subject_id: str | None,
        trace_id: str | None,
    ) -> DocumentGrant:
        """为一份文档添加授权;同逻辑文档 + 同主体重复授权幂等。"""
        record = self._require_manager(principal=principal, document_id=document_id)
        subject = _normalize_subject(
            subject_type=subject_type, subject_id=subject_id, record=record
        )
        existing = self._grants.find(
            tenant=record.tenant,
            file_name=record.file_name,
            subject_type=subject_type.value,
            subject_id=subject,
        )
        if existing is not None:
            return existing

        grant = DocumentGrant(
            tenant=record.tenant,
            file_name=record.file_name,
            subject_type=subject_type,
            subject_id=subject,
            granted_by=principal.id,
        )
        # 变更类审计先行:审计失败即中止,不留下"授权改了但没有证据"
        self._audit.record_required(
            tenant=record.tenant,
            principal_id=principal.id,
            action=AuditAction.DOCUMENT_GRANT,
            trace_id=trace_id,
            resource_type="document",
            resource_id=record.id,
            details={
                "file_name": record.file_name,
                "subject_type": subject_type.value,
                "subject_id": subject,
            },
        )
        self._grants.add(grant)
        self._invalidate_cache("document_grant_added")
        return grant

    def remove_grant(
        self,
        *,
        principal: Principal,
        document_id: str,
        grant_id: str,
        trace_id: str | None,
    ) -> DocumentGrant:
        """撤销一条授权;不存在或不属于该文档按 404,不泄露其他文档的授权。"""
        record = self._require_manager(principal=principal, document_id=document_id)
        grant = self._grants.get(grant_id)
        if (
            grant is None
            or grant.tenant != record.tenant
            or grant.file_name != record.file_name
        ):
            raise NotFoundError(
                "文档授权不存在",
                details={"document_id": document_id, "grant_id": grant_id},
            )
        self._audit.record_required(
            tenant=record.tenant,
            principal_id=principal.id,
            action=AuditAction.DOCUMENT_REVOKE,
            trace_id=trace_id,
            resource_type="document",
            resource_id=record.id,
            details={
                "file_name": record.file_name,
                "grant_id": grant_id,
                "subject_type": grant.subject_type.value,
                "subject_id": grant.subject_id,
            },
        )
        self._grants.delete(grant_id)
        self._invalidate_cache("document_grant_removed")
        return grant

    def _require_manager(
        self, *, principal: Principal, document_id: str
    ) -> DocumentRecord:
        """授权管理只允许所有者或租户管理员;其他主体一律 404。"""
        record = self._documents.get(document_id)
        if record is None or record.tenant != principal.tenant:
            raise NotFoundError(
                "知识库文档不存在",
                details={"document_id": document_id},
            )
        if principal.role is not Role.ADMIN and record.owner_id != principal.id:
            raise ScopeViolationError(
                "只有文档所有者或租户管理员可以管理文档授权",
                details={"document_id": document_id},
                audit={
                    "action": AuditAction.ACCESS_DENIED,
                    "resource_type": "document",
                    "resource_id": document_id,
                    "reason": "grant_manager_required",
                },
            )
        return record

    def _invalidate_cache(self, reason: str) -> None:
        if self._answer_cache is not None:
            self._answer_cache.clear(reason=reason)


def _normalize_subject(
    *,
    subject_type: GrantSubjectType,
    subject_id: str | None,
    record: DocumentRecord,
) -> str | None:
    """归一授权主体:租户级不存 subject_id;用户 / 部门必须给出非空标识。"""
    value = (subject_id or "").strip() or None
    if subject_type is GrantSubjectType.TENANT:
        if value not in (None, record.tenant):
            raise ScopeViolationError(
                "租户级授权只能指向文档所属租户",
                details={"tenant": value},
            )
        return None
    if value is None:
        raise ScopeViolationError(
            "用户 / 部门授权必须给出授权主体",
            details={"subject_type": subject_type.value},
        )
    return value
