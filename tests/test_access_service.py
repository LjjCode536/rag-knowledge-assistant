"""授权范围服务缝:主体 → 范围、对象级可见性(#27 的单一入口)。"""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError, ScopeViolationError
from app.domain.documents import DocumentFormat, DocumentRecord
from app.domain.identity import Principal, Role, ScopeNarrowing
from app.domain.retrieval import Scope
from app.repositories.memory import InMemoryDocumentRepository
from app.services.access import AccessService


def _document(document_id: str, tenant: str) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        file_name="handbook.md",
        file_type=DocumentFormat.MD,
        size_bytes=10,
        tenant=tenant,
    )


def _principal(tenant: str = "default") -> Principal:
    return Principal(id="usr_member", tenant=tenant, role=Role.MEMBER)


@pytest.fixture
def access() -> AccessService:
    repository = InMemoryDocumentRepository()
    repository.add(_document("doc-mine", "default"))
    repository.add(_document("doc-theirs", "other"))
    return AccessService(document_repository=repository)


def test_scope_for_fills_tenant_from_principal(access: AccessService) -> None:
    scope = access.scope_for(
        _principal(), ScopeNarrowing(department="人力部", version="v2")
    )

    assert scope == Scope(
        tenant="default", department="人力部", version="v2"
    )


def test_scope_for_own_document_is_allowed(access: AccessService) -> None:
    scope = access.scope_for(
        _principal(), ScopeNarrowing(document_id="doc-mine")
    )

    assert scope.document_id == "doc-mine"
    assert scope.tenant == "default"


def test_scope_for_document_in_another_tenant_is_403(access: AccessService) -> None:
    with pytest.raises(ScopeViolationError):
        access.scope_for(_principal(), ScopeNarrowing(document_id="doc-theirs"))


def test_scope_for_missing_document_is_403(access: AccessService) -> None:
    with pytest.raises(ScopeViolationError):
        access.scope_for(_principal(), ScopeNarrowing(document_id="no-such-doc"))


def test_require_document_returns_same_tenant_document(access: AccessService) -> None:
    record = access.require_document(
        scope=Scope(tenant="default"), document_id="doc-mine"
    )

    assert record.id == "doc-mine"


def test_require_document_hides_cross_tenant_and_missing_as_404(
    access: AccessService,
) -> None:
    with pytest.raises(NotFoundError):
        access.require_document(scope=Scope(tenant="default"), document_id="doc-theirs")
    with pytest.raises(NotFoundError):
        access.require_document(scope=Scope(tenant="default"), document_id="no-such-doc")
