"""授权范围服务缝:主体 → 范围、对象级可见性(ADR-0020 / 0021)。"""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError, ScopeViolationError
from app.domain.documents import DocumentFormat, DocumentRecord
from app.domain.grants import DocumentGrant, GrantSubjectType
from app.domain.identity import Principal, Role, ScopeNarrowing
from app.domain.retrieval import Scope
from app.repositories.memory import (
    InMemoryDocumentGrantRepository,
    InMemoryDocumentRepository,
)
from app.services.access import AccessService


def _document(
    document_id: str,
    tenant: str,
    *,
    owner_id: str | None = "usr_member",
    file_name: str = "handbook.md",
    latest: bool = True,
) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        file_name=file_name,
        file_type=DocumentFormat.MD,
        size_bytes=10,
        tenant=tenant,
        owner_id=owner_id,
        is_latest=latest,
    )


def _principal(
    tenant: str = "default", *, role: Role = Role.MEMBER, department: str | None = None
) -> Principal:
    return Principal(
        id="usr_member", tenant=tenant, role=role, department=department
    )


@pytest.fixture
def repository() -> InMemoryDocumentRepository:
    repository = InMemoryDocumentRepository()
    repository.add(_document("doc-mine", "default"))
    repository.add(_document("doc-shared", "default", owner_id="usr_other", file_name="shared.md"))
    repository.add(_document("doc-theirs", "other", owner_id="usr_other"))
    return repository


@pytest.fixture
def grants() -> InMemoryDocumentGrantRepository:
    return InMemoryDocumentGrantRepository()


@pytest.fixture
def access(
    repository: InMemoryDocumentRepository,
    grants: InMemoryDocumentGrantRepository,
) -> AccessService:
    return AccessService(
        document_repository=repository, grant_repository=grants
    )


def test_scope_for_fills_tenant_and_allowed_set_from_principal(
    access: AccessService,
) -> None:
    scope = access.scope_for(
        _principal(), ScopeNarrowing(department="人力部", version="v2")
    )

    assert scope.tenant == "default"
    assert scope.department == "人力部"
    assert scope.version == "v2"
    assert scope.document_ids == frozenset({"doc-mine"})


def test_default_is_unreadable_for_non_owner(access: AccessService) -> None:
    assert access.allowed_document_ids(_principal()) == frozenset({"doc-mine"})


def test_scope_for_own_document_is_allowed(access: AccessService) -> None:
    scope = access.scope_for(
        _principal(), ScopeNarrowing(document_id="doc-mine")
    )

    assert scope.document_id == "doc-mine"
    assert scope.tenant == "default"
    assert scope.document_ids == frozenset({"doc-mine"})


def test_scope_for_same_tenant_but_unauthorized_document_is_403(
    access: AccessService,
) -> None:
    with pytest.raises(ScopeViolationError):
        access.scope_for(_principal(), ScopeNarrowing(document_id="doc-shared"))


def test_scope_for_document_in_another_tenant_is_403(access: AccessService) -> None:
    with pytest.raises(ScopeViolationError):
        access.scope_for(_principal(), ScopeNarrowing(document_id="doc-theirs"))


def test_scope_for_missing_document_is_403(access: AccessService) -> None:
    with pytest.raises(ScopeViolationError):
        access.scope_for(_principal(), ScopeNarrowing(document_id="no-such-doc"))


def test_admin_reads_whole_tenant_but_not_other_tenant(
    access: AccessService,
) -> None:
    admin = _principal(role=Role.ADMIN)

    assert access.allowed_document_ids(admin) == frozenset(
        {"doc-mine", "doc-shared"}
    )


def test_user_grant_makes_document_readable(
    access: AccessService,
    grants: InMemoryDocumentGrantRepository,
) -> None:
    grants.add(
        DocumentGrant(
            tenant="default",
            file_name="shared.md",
            subject_type=GrantSubjectType.USER,
            subject_id="usr_member",
            granted_by="usr_other",
        )
    )

    assert "doc-shared" in access.allowed_document_ids(_principal())


def test_department_grant_compares_principal_department_not_document_field(
    access: AccessService,
    grants: InMemoryDocumentGrantRepository,
) -> None:
    grants.add(
        DocumentGrant(
            tenant="default",
            file_name="shared.md",
            subject_type=GrantSubjectType.DEPARTMENT,
            subject_id="市场部",
            granted_by="usr_other",
        )
    )

    assert "doc-shared" not in access.allowed_document_ids(_principal())
    assert "doc-shared" in access.allowed_document_ids(
        _principal(department="市场部")
    )


def test_tenant_grant_publishes_only_the_granted_logical_document(
    repository: InMemoryDocumentRepository,
    grants: InMemoryDocumentGrantRepository,
) -> None:
    access = AccessService(document_repository=repository, grant_repository=grants)
    grants.add(
        DocumentGrant(
            tenant="default",
            file_name="shared.md",
            subject_type=GrantSubjectType.TENANT,
            granted_by="usr_admin",
        )
    )

    # "发布到租户"只放行被授权的那份逻辑文档,不是全库
    assert access.allowed_document_ids(_principal()) == frozenset(
        {"doc-mine", "doc-shared"}
    )
    repository.add(
        _document("doc-secret", "default", owner_id="usr_admin", file_name="secret.md")
    )
    assert "doc-secret" not in access.allowed_document_ids(_principal())


def test_grant_survives_version_supersession(
    repository: InMemoryDocumentRepository,
    grants: InMemoryDocumentGrantRepository,
) -> None:
    access = AccessService(document_repository=repository, grant_repository=grants)
    grants.add(
        DocumentGrant(
            tenant="default",
            file_name="shared.md",
            subject_type=GrantSubjectType.USER,
            subject_id="usr_member",
            granted_by="usr_other",
        )
    )
    # 同名新版本上位,旧记录退位
    repository.save(
        _document(
            "doc-shared",
            "default",
            owner_id="usr_other",
            file_name="shared.md",
            latest=False,
        )
    )
    repository.add(
        _document("doc-v2", "default", owner_id="usr_other", file_name="shared.md")
    )

    allowed = access.allowed_document_ids(_principal())
    assert "doc-v2" in allowed
    assert "doc-shared" not in allowed


def test_require_document_returns_same_tenant_document(access: AccessService) -> None:
    record = access.require_document(
        scope=Scope(tenant="default", document_ids=frozenset({"doc-mine"})),
        document_id="doc-mine",
    )

    assert record.id == "doc-mine"


def test_require_document_hides_cross_tenant_and_missing_as_404(
    access: AccessService,
) -> None:
    with pytest.raises(NotFoundError) as cross:
        access.require_document(
            scope=Scope(tenant="default", document_ids=frozenset({"doc-mine"})),
            document_id="doc-theirs",
        )
    assert cross.value.audit is not None
    assert cross.value.audit["action"].value == "access.cross_tenant"

    with pytest.raises(NotFoundError):
        access.require_document(
            scope=Scope(tenant="default", document_ids=frozenset({"doc-mine"})),
            document_id="no-such-doc",
        )
