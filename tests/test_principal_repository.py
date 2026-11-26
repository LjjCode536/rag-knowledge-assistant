"""主体仓储的内存与 PostgreSQL 实现行为一致(ADR-0020,issue #26)。

真库不可达时 PG 用例整批跳过;内存实现始终验证。
"""

from __future__ import annotations

import psycopg
import pytest

from app.repositories.memory import InMemoryPrincipalRepository
from app.repositories.postgres import PgDatabase, PgPrincipalRepository
from tests.support import ADMIN, MEMBER, OTHER_TENANT, TEST_PASSWORD_HASH, seed_account


def test_memory_repository_upsert_is_idempotent():
    repository = InMemoryPrincipalRepository([MEMBER])

    repository.upsert(MEMBER)

    assert repository.list() == [MEMBER]
    assert repository.find_by_username("member") == MEMBER
    assert repository.get("usr_member") == MEMBER


def test_memory_repository_upsert_updates_existing_principal():
    repository = InMemoryPrincipalRepository([MEMBER])
    updated = seed_account("usr_member", "member", department="平台部")

    repository.upsert(updated)

    assert repository.get("usr_member") == updated
    assert repository.find_by_username("member") == updated


def test_memory_repository_rejects_ambiguous_username():
    repository = InMemoryPrincipalRepository([MEMBER])
    duplicate = seed_account("usr_other", "member")

    with pytest.raises(ValueError, match="登录名"):
        repository.upsert(duplicate)


def test_memory_repository_unknown_lookups_return_none():
    repository = InMemoryPrincipalRepository()

    assert repository.find_by_username("nobody") is None
    assert repository.get("usr_missing") is None
    assert repository.list() == []


def test_pg_repository_matches_memory_contract(test_pg_database):
    db = PgDatabase(test_pg_database.dsn)
    try:
        repository = PgPrincipalRepository(db)

        assert repository.find_by_username("member") is None
        repository.upsert(MEMBER)
        repository.upsert(MEMBER)
        assert repository.list() == [MEMBER]
        assert repository.find_by_username("member") == MEMBER
        assert repository.get("usr_member") == MEMBER

        repository.upsert(ADMIN)
        assert {account.principal.id for account in repository.list()} == {
            "usr_member",
            "usr_admin",
        }

        updated = seed_account("usr_member", "member", department="平台部")
        repository.upsert(updated)
        assert repository.get("usr_member") == updated
        assert repository.find_by_username("member") == updated

        # 与内存实现同语义:重复登录名是程序错误(异常类型随存储而异)
        with pytest.raises(psycopg.errors.UniqueViolation):
            repository.upsert(seed_account("usr_other", "member"))
    finally:
        db.close()


def test_pg_seed_script_is_idempotent(test_pg_database, monkeypatch):
    from scripts.seed_principals import main as seed_main

    monkeypatch.setenv("SEED_MEMBER_PASSWORD", "seed-password")
    monkeypatch.delenv("SEED_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_OTHER_MEMBER_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_PLATFORM_PASSWORD", raising=False)

    assert seed_main(["--yes", "--database-url", test_pg_database.dsn]) == 0
    assert seed_main(["--yes", "--database-url", test_pg_database.dsn]) == 0

    db = PgDatabase(test_pg_database.dsn)
    try:
        accounts = PgPrincipalRepository(db).list()
    finally:
        db.close()
    assert [account.principal.username for account in accounts] == ["member"]
    assert accounts[0].password_hash != TEST_PASSWORD_HASH
    assert accounts[0].principal.tenant == "default"


def test_pg_seed_script_requires_explicit_confirmation(test_pg_database, monkeypatch, capsys):
    from scripts.seed_principals import main as seed_main

    monkeypatch.setenv("SEED_MEMBER_PASSWORD", "seed-password")
    for name in ("ADMIN", "OTHER_MEMBER", "PLATFORM"):
        monkeypatch.delenv(f"SEED_{name}_PASSWORD", raising=False)

    assert seed_main(["--database-url", test_pg_database.dsn]) == 2

    db = PgDatabase(test_pg_database.dsn)
    try:
        assert PgPrincipalRepository(db).list() == []
    finally:
        db.close()
    assert "--yes" in capsys.readouterr().err


def test_other_tenant_account_keeps_own_tenant():
    repository = InMemoryPrincipalRepository([OTHER_TENANT])

    account = repository.find_by_username("member-other")

    assert account is not None
    assert account.principal.tenant == "other"
