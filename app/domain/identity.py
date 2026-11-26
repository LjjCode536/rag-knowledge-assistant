"""身份与主体:登录、短期 JWT 与测试身份夹具共用的领域模型(ADR-0020)。

主体(Principal)是**一次请求中被认证的身份**,携带租户、角色与部门;
它是服务端构造授权范围的唯一依据,不来自请求体或查询参数。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import AppError, ErrorCode


class Role(StrEnum):
    """租户内角色;跨租户只有平台管理员。"""

    MEMBER = "member"
    ADMIN = "admin"
    PLATFORM_OPERATOR = "platform_operator"


class PrincipalKind(StrEnum):
    """主体种类:人类用户与机器服务账号(服务账号 v1 只建模、不提供签发入口)。"""

    USER = "user"
    SERVICE = "service"


class Principal(BaseModel):
    """已认证主体:token claims 的领域视图。

    ``username`` 只对本地种子账号有值;OIDC / 服务账号可以没有。
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    kind: PrincipalKind = PrincipalKind.USER
    tenant: str = Field(min_length=1)
    role: Role
    department: str | None = None
    username: str | None = None
    display_name: str | None = None


class ScopeNarrowing(BaseModel):
    """HTTP 请求可声明的收窄条件:只有非权限字段,不含租户。

    租户来自主体,由服务端补上;请求体里出现 ``tenant`` 即 422。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    department: str | None = None
    doc_type: str | None = None
    version: str | None = None
    document_id: str | None = None


class PrincipalAccount(BaseModel):
    """主体仓储里的一条账号记录:主体 + 口令哈希。

    口令哈希只在这一层出现;认证服务验过口令后只把 :class:`Principal`
    带进请求,绝不下发哈希。
    """

    model_config = ConfigDict(frozen=True)

    principal: Principal
    # 服务账号 v1 只建模、不签发:它可以没有口令哈希,登录入口只认用户主体。
    password_hash: str | None = None


class UnauthenticatedError(AppError):
    """未携带 / 无效 / 过期的凭证,或登录口令错误;一律 401。"""

    code = ErrorCode.UNAUTHENTICATED
    status_code = 401
    default_message = "未认证或凭证已失效"
