"""HTTP 鉴权依赖:把 Bearer token 解析成主体(ADR-0020)。

路由只依赖本模块得到主体与授权范围;任何身份字段都不从请求体 / 查询参数读取。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.exceptions import RequestValidationError

from app.domain.documents import DocumentRecord
from app.domain.identity import Principal, UnauthenticatedError
from app.domain.retrieval import Scope
from app.services.access import AccessService
from app.services.auth import AuthService

_BEARER_PREFIX = "bearer "


def reject_tenant_parameter(request: Request) -> None:
    """普通端点不接受调用方声明的租户:携带 ``tenant`` 查询参数即 422。

    FastAPI 默认忽略未声明的查询参数,因此显式校验;错误交给统一校验
    处理器,返回既有的 ``VALIDATION_ERROR`` 信封。租户只能来自主体。
    """
    if "tenant" in request.query_params:
        raise RequestValidationError(
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("query", "tenant"),
                    "msg": "Extra inputs are not permitted",
                    "input": request.query_params["tenant"],
                }
            ]
        )


def require_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """解析 ``Authorization: Bearer <JWT>``;缺失 / 无效 / 过期一律 401。"""
    if authorization is None or not authorization.strip():
        raise UnauthenticatedError("缺少访问凭证")
    value = authorization.strip()
    if not value.lower().startswith(_BEARER_PREFIX):
        raise UnauthenticatedError("访问凭证格式不正确")
    token = value[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise UnauthenticatedError("缺少访问凭证")
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
