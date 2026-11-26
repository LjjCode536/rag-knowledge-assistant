"""显式创建种子账号(ADR-0020)。

服务启动**从不**自动建号;本脚本是唯一入口,且必须带 ``--yes`` 确认。
口令只从环境变量读取,脚本只打印账号与角色,不打印口令:

    SEED_MEMBER_PASSWORD=... \\
    SEED_ADMIN_PASSWORD=... \\
    uv run python scripts/seed_principals.py --yes

每个账号的完整环境变量(括号内为默认值):
    SEED_<NAME>_USERNAME / SEED_<NAME>_PASSWORD / SEED_<NAME>_TENANT /
    SEED_<NAME>_DEPARTMENT / SEED_<NAME>_DISPLAY_NAME

    NAME = MEMBER(member / default) / ADMIN(admin / default) /
           OTHER_MEMBER(member-other / other) / PLATFORM(operator / platform)

未设置 ``SEED_<NAME>_PASSWORD`` 的账号跳过。重复执行按主体 id 幂等覆盖:
账号已存在时只更新资料与口令哈希,不产生重复行。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from app.core.config import Settings
from app.domain.identity import Principal, PrincipalAccount, PrincipalKind, Role
from app.repositories.postgres import PgDatabase, PgPrincipalRepository
from app.services.auth import PasswordHasherService


@dataclass(frozen=True, slots=True)
class SeedSpec:
    """一个种子账号的静态定义;口令单独从环境变量取。"""

    env_name: str
    principal_id: str
    username: str
    tenant: str
    role: Role
    department: str | None
    display_name: str


SEED_SPECS: tuple[SeedSpec, ...] = (
    SeedSpec(
        env_name="MEMBER",
        principal_id="usr_member",
        username="member",
        tenant="default",
        role=Role.MEMBER,
        department="研发部",
        display_name="种子成员",
    ),
    SeedSpec(
        env_name="ADMIN",
        principal_id="usr_admin",
        username="admin",
        tenant="default",
        role=Role.ADMIN,
        department="平台部",
        display_name="租户管理员",
    ),
    SeedSpec(
        env_name="OTHER_MEMBER",
        principal_id="usr_other_member",
        username="member-other",
        tenant="other",
        role=Role.MEMBER,
        department="市场部",
        display_name="其他租户成员",
    ),
    SeedSpec(
        env_name="PLATFORM",
        principal_id="usr_platform",
        username="operator",
        tenant="platform",
        role=Role.PLATFORM_OPERATOR,
        department=None,
        display_name="平台管理员",
    ),
)


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _account_from_env(
    spec: SeedSpec, password_hasher: PasswordHasherService
) -> PrincipalAccount | None:
    password = _env(f"SEED_{spec.env_name}_PASSWORD")
    if password is None:
        return None
    username = _env(f"SEED_{spec.env_name}_USERNAME") or spec.username
    tenant = _env(f"SEED_{spec.env_name}_TENANT") or spec.tenant
    department = _env(f"SEED_{spec.env_name}_DEPARTMENT")
    if department is None:
        department = spec.department
    display_name = _env(f"SEED_{spec.env_name}_DISPLAY_NAME") or spec.display_name
    principal = Principal(
        id=spec.principal_id,
        kind=PrincipalKind.USER,
        tenant=tenant,
        role=spec.role,
        department=department,
        username=username,
        display_name=display_name,
    )
    return PrincipalAccount(
        principal=principal,
        password_hash=password_hasher.hash(password),
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="显式创建 / 更新种子账号(幂等)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认执行;不加此参数只打印计划,不写库",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="覆盖配置里的 DATABASE_URL",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    settings = Settings()
    database_url = args.database_url or settings.database_url
    if not database_url:
        print("缺少 DATABASE_URL:种子账号写入 PostgreSQL,不落内存。", file=sys.stderr)
        return 2

    hasher = PasswordHasherService()
    accounts = [
        account
        for spec in SEED_SPECS
        if (account := _account_from_env(spec, hasher)) is not None
    ]
    if not accounts:
        print(
            "没有可创建的账号:至少设置一个 SEED_<NAME>_PASSWORD"
            "(MEMBER / ADMIN / OTHER_MEMBER / PLATFORM)。",
            file=sys.stderr,
        )
        return 2

    print("将幂等写入以下种子账号(口令从环境变量读取,不在此显示):")
    for account in accounts:
        principal = account.principal
        department = principal.department or "-"
        print(
            f"  - {principal.username} / tenant={principal.tenant} / "
            f"role={principal.role.value} / department={department}"
        )
    if not args.yes:
        print("未执行。确认后加 --yes 重跑。", file=sys.stderr)
        return 2

    database = PgDatabase(database_url)
    try:
        repository = PgPrincipalRepository(database)
        for account in accounts:
            repository.upsert(account)
        print(f"完成:{len(accounts)} 个种子账号已写入。")
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
