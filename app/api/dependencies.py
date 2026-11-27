"""HTTP 鉴权依赖:把 Bearer token 解析成主体(ADR-0020)。

路由只依赖本模块得到主体与授权范围;任何身份字段都不从请求体 / 查询参数读取。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import BadRequestError, ScopeViolationError
from app.core.tracing import trace_id_of
from app.domain.audit import AuditAction
from app.domain.documents import DocumentRecord
from app.domain.identity import Principal, Role, UnauthenticatedError
from app.domain.retrieval import Scope
from app.services.access import AccessService
from app.services.audit import AuditService
from app.services.auth import AuthService

# OpenAPI 的鉴权声明与真实校验同源:auto_error=False 让缺失 / 非 Bearer 凭证
# 仍由本模块统一转成 401 UNAUTHENTICATED,而不是框架默认的 403。
_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="登录端点签发的短期 JWT;租户、角色与部门均来自 token claims。",
)


def reject_tenant_parameter(request: Request) -> None:
    """普通端点不接受调用方声明的租户:携带 ``tenant`` 查询参数即 403。

    租户只能来自主体(ADR-0020);跨租户只能走管理端点显式选择租户并逐次
    留痕。这里不再是 422 校验错误,而是明确的越权拒绝。
    """
    if "tenant" in request.query_params:
        tenant = request.query_params["tenant"]
        raise ScopeViolationError(
            "普通端点不接受租户参数",
            details={"tenant": tenant},
            audit={
                "action": AuditAction.ACCESS_DENIED,
                "resource_type": "tenant",
                "resource_id": tenant,
                "reason": "tenant_parameter_on_non_admin_endpoint",
                "path": request.url.path,
            },
        )


async def reject_tenant_form_field(request: Request) -> None:
    """上传表单不接受 ``tenant``:租户只来自主体,携带即 422(ADR-0020)。

    只在表单型请求上解析表单;Starlette 会缓存解析结果,后续端点读取
    ``UploadFile`` 与其它字段时不会重复消费请求体。
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith(
        ("multipart/form-data", "application/x-www-form-urlencoded")
    ):
        return
    form = await request.form()
    if "tenant" in form:
        raise RequestValidationError(
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("body", "tenant"),
                    "msg": "Extra inputs are not permitted",
                    "input": form["tenant"],
                }
            ]
        )


def require_principal(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ] = None,
) -> Principal:
    """解析 ``Authorization: Bearer <JWT>``;缺失 / 无效 / 过期一律 401。"""
    if credentials is None or not credentials.credentials.strip():
        raise UnauthenticatedError("缺少访问凭证")
    token = credentials.credentials.strip()
    auth_service: AuthService = request.app.state.auth_service
    principal = auth_service.authenticate(token)
    # 路由与服务层从 request.state 取主体,不重复解析 token
    request.state.principal = principal
    return principal


def require_scope(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> Scope:
    """主体 → 授权范围:租户是硬边界,读路径不带收窄条件。"""
    access_service: AccessService = request.app.state.access_service
    return access_service.scope_for(principal, None)


def require_document_in_scope(
    request: Request,
    document_id: str,
    scope: Annotated[Scope, Depends(require_scope)],
) -> DocumentRecord:
    """对象级读路径的统一入口:跨租户与不存在一律 404,不泄露存在性。"""
    access_service: AccessService = request.app.state.access_service
    return access_service.require_document(scope=scope, document_id=document_id)


def require_writable_document(
    request: Request,
    document_id: str,
    principal: Annotated[Principal, Depends(require_principal)],
) -> DocumentRecord:
    """对象级写路径入口:切片 / 向量化 / 删除只允许所有者或租户管理员。"""
    access_service: AccessService = request.app.state.access_service
    return access_service.require_writable_document(
        principal=principal, document_id=document_id
    )


def require_admin_tenant(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> str:
    """管理端点的租户解析:普通管理员只看本租户,平台管理员显式选择租户。

    - 租户 ``admin``:租户来自 token;携带 ``tenant`` 参数是越权(403);
    - ``platform_operator``:必须显式给出 ``tenant``,每次成功访问写审计;
    - 其他角色:403,不泄露管理端点细节。
    """
    requested = request.query_params.get("tenant")
    if principal.role is Role.ADMIN:
        if requested is not None:
            raise ScopeViolationError(
                "普通管理员不接受租户参数",
                details={"tenant": requested},
                audit={
                    "action": AuditAction.ACCESS_DENIED,
                    "resource_type": "tenant",
                    "resource_id": requested,
                    "reason": "tenant_parameter_for_tenant_admin",
                    "path": request.url.path,
                },
            )
        return principal.tenant
    if principal.role is Role.PLATFORM_OPERATOR:
        tenant = (requested or "").strip()
        if not tenant:
            raise BadRequestError(
                "平台管理员必须显式指定租户",
                details={"parameter": "tenant"},
            )
        audit_service: AuditService = request.app.state.audit_service
        audit_service.record_required(
            tenant=tenant,
            principal_id=principal.id,
            action=AuditAction.PLATFORM_TENANT_ACCESS,
            trace_id=trace_id_of(request),
            resource_type="tenant",
            resource_id=tenant,
            details={"path": request.url.path},
        )
        return tenant
    raise ScopeViolationError(
        "仅管理员可访问管理端点",
        details={"role": principal.role.value},
        audit={
            "action": AuditAction.ACCESS_DENIED,
            "resource_type": "admin_endpoint",
            "resource_id": request.url.path,
            "reason": "admin_required",
        },
    )
