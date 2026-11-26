"""身份端点:登录换短期 JWT 与只读的当前主体(ADR-0020)。

登录是公开端点;当前主体必须携带真实 token。租户、角色与部门全部来自
token claims,请求体不参与身份判定。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import reject_tenant_parameter, require_principal
from app.core.envelope import ApiEnvelope, success_envelope
from app.core.tracing import trace_id_of
from app.domain.identity import Principal, PrincipalKind, Role
from app.services.auth import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """登录请求:只有登录名与口令;携带其他字段即 422。"""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class PrincipalView(BaseModel):
    """当前主体视图:租户 / 角色 / 部门由服务端从 token 解析。"""

    id: str
    username: str | None = None
    kind: PrincipalKind
    tenant: str
    role: Role
    department: str | None = None
    display_name: str | None = None

    @classmethod
    def of(cls, principal: Principal) -> PrincipalView:
        return cls(
            id=principal.id,
            username=principal.username,
            kind=principal.kind,
            tenant=principal.tenant,
            role=principal.role,
            department=principal.department,
            display_name=principal.display_name,
        )


class LoginView(BaseModel):
    """登录结果:短期 token + 有效期 + 解析后的主体。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    principal: PrincipalView


@router.post("/login", response_model=ApiEnvelope[LoginView], summary="登录换取短期 JWT")
async def login(request: Request, body: LoginRequest) -> ApiEnvelope[LoginView]:
    service: AuthService = request.app.state.auth_service
    issued = service.login(body.username, body.password)
    data = LoginView(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        principal=PrincipalView.of(issued.principal),
    )
    return success_envelope(data, trace_id=trace_id_of(request), message="登录成功")


@router.get(
    "/me",
    response_model=ApiEnvelope[PrincipalView],
    summary="当前主体(租户 / 角色 / 部门)",
    dependencies=[Depends(reject_tenant_parameter)],
)
async def current_principal(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> ApiEnvelope[PrincipalView]:
    return success_envelope(PrincipalView.of(principal), trace_id=trace_id_of(request))
