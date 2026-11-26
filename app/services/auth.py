"""身份服务:口令哈希、短期 JWT 签发 / 校验与登录(ADR-0020)。

密码学不手写:签名与校验用 PyJWT,口令哈希用 argon2。服务只信任 token
claims;请求体 / 查询参数里的任何身份字段都不参与认证。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.domain.identity import (
    Principal,
    PrincipalKind,
    Role,
    UnauthenticatedError,
)
from app.domain.repositories import PrincipalRepository

# 登录名不存在时也走一次同代价的校验:避免用响应时间枚举账号。
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$UTUobrCxYx0/V8DxJZTyPg"
    "$YqzQ4jVadIztazUGFHPkFo1/4WBwm1jElWSWvfpskvg"
)

# ``department`` 允许显式为 null(平台管理员 / 无部门主体),所以不放进 PyJWT
# 的 require(它把 null 当缺失),在 claims 映射处单独校验"键存在"。
_REQUIRED_CLAIMS = ("sub", "tenant", "role", "kind", "exp", "iat", "iss", "aud")


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """一次签发的凭证:token 本体 + 有效秒数 + 解码后的主体。"""

    access_token: str
    expires_in: int
    principal: Principal


class PasswordHasherService:
    """argon2 口令哈希与校验;种子脚本与登录共用同一口径。"""

    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


class TokenService:
    """HS256 短期 JWT:本地演示签发;生产路径应换成 OIDC / JWKS。"""

    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        ttl_seconds: int,
        algorithm: str = "HS256",
    ) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._ttl_seconds = ttl_seconds
        self._algorithm = algorithm

    @classmethod
    def from_settings(cls, settings, *, ttl_seconds: int | None = None) -> TokenService:
        """从应用配置装配:签名 / 签发方 / 受众 / 有效期只在一处映射。"""
        return cls(
            secret=settings.auth_jwt_secret,
            issuer=settings.auth_jwt_issuer,
            audience=settings.auth_jwt_audience,
            ttl_seconds=(
                settings.auth_token_ttl_seconds if ttl_seconds is None else ttl_seconds
            ),
        )

    def issue(self, principal: Principal) -> IssuedToken:
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=self._ttl_seconds)
        claims = {
            "sub": principal.id,
            "tenant": principal.tenant,
            "role": principal.role.value,
            "department": principal.department,
            "kind": principal.kind.value,
            "username": principal.username,
            "display_name": principal.display_name,
            "iat": issued_at,
            "exp": expires_at,
            "iss": self._issuer,
            "aud": self._audience,
        }
        token = jwt.encode(claims, self._secret, algorithm=self._algorithm)
        return IssuedToken(
            access_token=token,
            expires_in=self._ttl_seconds,
            principal=principal,
        )

    def decode(self, token: str) -> Principal:
        """校验签名 / 过期 / 签发方 / 受众,返回主体;任何失败都是 401。"""
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": list(_REQUIRED_CLAIMS)},
            )
        except jwt.PyJWTError as exc:
            raise UnauthenticatedError("凭证无效或已过期") from exc
        return _principal_from_claims(claims)


def _principal_from_claims(claims: dict) -> Principal:
    """claims → 主体;字段缺失 / 枚举值未知一律视为无效凭证。"""
    try:
        if "department" not in claims:
            raise ValueError("department 声明缺失")
        principal_id = claims["sub"]
        tenant = claims["tenant"]
        role = Role(claims["role"])
        kind = PrincipalKind(claims["kind"])
        department = claims.get("department")
        username = claims.get("username")
        display_name = claims.get("display_name")
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("sub 必须是非空字符串")
        if not isinstance(tenant, str) or not tenant:
            raise ValueError("tenant 必须是非空字符串")
        return Principal(
            id=principal_id,
            kind=kind,
            tenant=tenant,
            role=role,
            department=department if isinstance(department, str) else None,
            username=username if isinstance(username, str) else None,
            display_name=display_name if isinstance(display_name, str) else None,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise UnauthenticatedError("凭证声明不完整") from exc


class AuthService:
    """登录:口令换 token。账号只来自主体仓储,不来自请求声明。"""

    def __init__(
        self,
        *,
        principal_repository: PrincipalRepository,
        token_service: TokenService,
        password_hasher: PasswordHasherService | None = None,
    ) -> None:
        self._principals = principal_repository
        self._tokens = token_service
        self._hasher = password_hasher or PasswordHasherService()

    def login(self, username: str, password: str) -> IssuedToken:
        account = self._principals.find_by_username(username.strip())
        if account is None:
            # 同代价校验:账号不存在与口令错误对外不可区分
            self._hasher.verify(_DUMMY_PASSWORD_HASH, password)
            raise UnauthenticatedError("账号或口令不正确")
        if (
            account.principal.kind is not PrincipalKind.USER
            or account.password_hash is None
            or not self._hasher.verify(account.password_hash, password)
        ):
            raise UnauthenticatedError("账号或口令不正确")
        return self._tokens.issue(account.principal)

    def authenticate(self, token: str) -> Principal:
        return self._tokens.decode(token)
