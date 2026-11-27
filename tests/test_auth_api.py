"""身份底座:登录、当前主体与 401(ADR-0020,issue #26)。

测试用与配置同一密钥签发**真实** JWT,不覆盖鉴权依赖;匿名客户端仍能
断言 401。默认 ``client`` 夹具以种子租户 ``default`` 的 member 身份请求。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from app.services.auth import TokenService
from tests.support import TEST_PASSWORD

LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"


def test_login_returns_short_lived_jwt_and_principal(client):
    response = client.post(
        LOGIN_URL,
        json={"username": "member", "password": TEST_PASSWORD},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["access_token"]
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert data["principal"] == {
        "id": "usr_member",
        "username": "member",
        "kind": "user",
        "tenant": "default",
        "role": "member",
        "department": "研发部",
        "display_name": "member",
    }
    # 口令哈希绝不下发
    assert "password_hash" not in response.text


def test_wrong_password_returns_401_with_stable_code(client):
    response = client.post(
        LOGIN_URL,
        json={"username": "member", "password": "wrong-password"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "UNAUTHENTICATED"
    assert body["data"] is None
    assert "password" not in body["message"].lower()


def test_unknown_username_is_indistinguishable_from_wrong_password(client):
    response = client.post(
        LOGIN_URL,
        json={"username": "nobody", "password": "whatever"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_login_rejects_extra_identity_fields(client):
    response = client.post(
        LOGIN_URL,
        json={
            "username": "member",
            "password": TEST_PASSWORD,
            "tenant": "other",
            "role": "admin",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_current_principal_comes_from_token(client):
    response = client.get(ME_URL)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["tenant"] == "default"
    assert data["role"] == "member"
    assert data["department"] == "研发部"


def test_missing_token_returns_401(anonymous_client):
    response = anonymous_client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_expired_token_returns_401(app, anonymous_client):
    settings = app.state.settings
    expired_tokens = TokenService.from_settings(settings, ttl_seconds=-1)
    issued = expired_tokens.issue(app.state.principal_repository.list()[0].principal)

    response = anonymous_client.get(
        ME_URL,
        headers={"Authorization": f"Bearer {issued.access_token}"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_token_without_department_claim_returns_401(app, anonymous_client):
    settings = app.state.settings
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "usr_member",
            "tenant": "default",
            "role": "member",
            "kind": "user",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iss": settings.auth_jwt_issuer,
            "aud": settings.auth_jwt_audience,
        },
        settings.auth_jwt_secret,
        algorithm="HS256",
    )

    response = anonymous_client.get(
        ME_URL,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_tampered_token_returns_401(anonymous_client):
    response = anonymous_client.get(
        ME_URL,
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_non_bearer_scheme_returns_401(anonymous_client):
    response = anonymous_client.get(
        ME_URL,
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_openapi_declares_bearer_auth_only_on_protected_endpoints(client):
    """契约与真实校验同步:受保护端点声明 HTTPBearer,登录页保持公开。"""
    spec = client.app.openapi()

    schemes = spec["components"]["securitySchemes"]
    assert schemes["HTTPBearer"]["type"] == "http"
    assert schemes["HTTPBearer"]["scheme"] == "bearer"
    assert spec["paths"]["/api/v1/chat"]["post"]["security"] == [{"HTTPBearer": []}]
    assert spec["paths"]["/api/v1/documents"]["get"]["security"] == [{"HTTPBearer": []}]
    assert not spec["paths"]["/api/v1/auth/login"]["post"].get("security")


def test_protected_endpoint_requires_authentication(anonymous_client):
    response = anonymous_client.get("/api/v1/documents")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_health_stays_public(anonymous_client):
    response = anonymous_client.get("/health")

    assert response.status_code == 200
    assert response.json()["code"] == "OK"


def test_admin_and_other_tenant_clients_carry_their_own_scope(admin_client, other_tenant_client):
    admin = admin_client.get(ME_URL).json()["data"]
    other = other_tenant_client.get(ME_URL).json()["data"]

    assert (admin["tenant"], admin["role"]) == ("default", "admin")
    assert (other["tenant"], other["role"]) == ("other", "member")
