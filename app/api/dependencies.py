"""HTTP 鉴权依赖:把 Bearer token 解析成主体(ADR-0020)。

路由只依赖本模块得到主体;任何身份字段都不从请求体 / 查询参数读取。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, Request

from app.domain.identity import Principal, UnauthenticatedError
from app.services.auth import AuthService

_BEARER_PREFIX = "bearer "


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
