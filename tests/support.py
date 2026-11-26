"""测试辅助:解析 structlog JSON 输出与身份夹具(真实 JWT)。

身份夹具只做两件事:用与配置同一密钥签发**真实** token,并把 token 放进
``TestClient`` 的默认请求头 —— 不覆盖鉴权依赖,既有测试继续走真实鉴权路径
(ADR-0020)。默认主体是种子租户 ``default`` 的 ``member``。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.domain.identity import Principal, PrincipalAccount, PrincipalKind, Role
from app.repositories.memory import InMemoryPrincipalRepository

# 测试口令只存在于测试代码;哈希用 argon2 预先算好,避免每个夹具重复计算。
TEST_PASSWORD = "test-password"
TEST_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$6IPw7obADzaCYeusfeceCw"
    "$21VAOmKvg3IKadFuqEaUegb+ko1dlbA1kxMobmUNT4I"
)


def seed_account(
    principal_id: str,
    username: str,
    *,
    tenant: str = "default",
    role: Role = Role.MEMBER,
    department: str | None = "研发部",
    display_name: str | None = None,
) -> PrincipalAccount:
    """构造一条带已知口令的种子账号。"""
    return PrincipalAccount(
        principal=Principal(
            id=principal_id,
            kind=PrincipalKind.USER,
            tenant=tenant,
            role=role,
            department=department,
            username=username,
            display_name=display_name or username,
        ),
        password_hash=TEST_PASSWORD_HASH,
    )


MEMBER = seed_account("usr_member", "member")
ADMIN = seed_account("usr_admin", "admin", role=Role.ADMIN, department="平台部")
OTHER_TENANT = seed_account(
    "usr_other_member", "member-other", tenant="other", department="市场部"
)
PLATFORM_OPERATOR = seed_account(
    "usr_platform",
    "operator",
    tenant="platform",
    role=Role.PLATFORM_OPERATOR,
    department=None,
)

SEED_ACCOUNTS: tuple[PrincipalAccount, ...] = (
    MEMBER,
    ADMIN,
    OTHER_TENANT,
    PLATFORM_OPERATOR,
)


def seed_principal_repository() -> InMemoryPrincipalRepository:
    """内存种子夹具:服务启动不会自动建号,测试显式注入。"""
    return InMemoryPrincipalRepository(SEED_ACCOUNTS)


def auth_header(app, principal: Principal = MEMBER.principal) -> dict[str, str]:
    """用应用的同一密钥签发真实 token。"""
    issued = app.state.token_service.issue(principal)
    return {"Authorization": f"Bearer {issued.access_token}"}


def authorized_client(
    app,
    principal: Principal = MEMBER.principal,
    **kwargs,
) -> TestClient:
    """成员客户端:默认带种子租户 member 的真实 token。"""
    kwargs.setdefault("raise_server_exceptions", False)
    return TestClient(app, headers=auth_header(app, principal), **kwargs)


def anonymous_client(app, **kwargs) -> TestClient:
    """匿名客户端:不带任何凭证,用于断言 401。"""
    kwargs.setdefault("raise_server_exceptions", False)
    return TestClient(app, **kwargs)


def json_log_entries(output: str) -> list[dict]:
    entries: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            entries.append(json.loads(line))
    return entries
