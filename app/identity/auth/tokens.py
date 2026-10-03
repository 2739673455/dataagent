"""JWT 签发、声明验证及令牌数据类型。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

import jwt

from app.identity import errors as auth_error
from app.identity.models.account import User
from app.shared.config.app_config import AuthConfig


@dataclass(frozen=True)
class AccessTokenClaims:
    """已验证的访问令牌载荷。"""

    user_id: int
    auth_version: int


@dataclass(frozen=True)
class RefreshTokenClaims:
    """已验证的刷新令牌载荷。"""

    user_id: int
    token_id: UUID
    family_id: UUID


@dataclass(frozen=True)
class TokenPair:
    """访问令牌与刷新令牌。"""

    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_expires_in: int


class JWTCodec:
    """应用 JWT 编解码器。"""

    def __init__(self, config: AuthConfig) -> None:
        """绑定 JWT 签名与生命周期配置。"""
        self._config = config
        self._secret = config.jwt_secret.get_secret_value()

    def issue_access_token(self, user: User, now: datetime) -> str:
        """签发短期访问令牌。"""
        expires_at = now + timedelta(minutes=self._config.access_token_minutes)
        payload: dict[str, Any] = {
            "sub": str(user.id),
            "auth_version": user.auth_version,
            "token_type": "access",
            "iat": now,
            "exp": expires_at,
            "iss": self._config.issuer,
        }
        return jwt.encode(
            payload,
            self._secret,
            algorithm=self._config.jwt_algorithm,
        )

    def issue_refresh_token(
        self,
        user_id: int,
        token_id: UUID,
        family_id: UUID,
        now: datetime,
    ) -> str:
        """签发长期刷新令牌。"""
        return jwt.encode(
            {
                "sub": str(user_id),
                "jti": str(token_id),
                "family_id": str(family_id),
                "token_type": "refresh",
                "iat": now,
                "exp": now + timedelta(days=self._config.refresh_token_days),
                "iss": self._config.issuer,
            },
            self._secret,
            algorithm=self._config.jwt_algorithm,
        )

    def decode_access_token(self, token: str) -> AccessTokenClaims:
        """校验并解析访问令牌。"""
        payload = self._decode(
            token,
            "access",
            required_claims={"sub", "auth_version", "token_type", "iat", "exp", "iss"},
        )
        return AccessTokenClaims(
            user_id=self._parse_user_id(payload),
            auth_version=self._parse_auth_version(payload),
        )

    def decode_refresh_token(self, token: str) -> RefreshTokenClaims:
        """校验并解析刷新令牌。"""
        payload = self._decode(
            token,
            "refresh",
            required_claims={
                "sub",
                "jti",
                "family_id",
                "token_type",
                "iat",
                "exp",
                "iss",
            },
        )
        return RefreshTokenClaims(
            user_id=self._parse_user_id(payload),
            token_id=self._parse_uuid(payload, "jti"),
            family_id=self._parse_uuid(payload, "family_id"),
        )

    def _decode(
        self,
        token: str,
        expected_type: str,
        *,
        required_claims: set[str],
    ) -> dict[str, Any]:
        """验证 JWT 签名、标准声明与令牌类型。"""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._config.jwt_algorithm],
                issuer=self._config.issuer,
                leeway=5,
                options={"require": sorted(required_claims)},
            )
        except jwt.PyJWTError as exc:
            raise auth_error.InvalidTokenError from exc
        if payload.get("token_type") != expected_type:
            raise auth_error.InvalidTokenError(detail="非预期的令牌类型")
        return cast(dict[str, Any], payload)

    @staticmethod
    def _parse_user_id(payload: dict[str, Any]) -> int:
        """解析用户主键声明。"""
        try:
            user_id = int(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise auth_error.InvalidTokenError(detail="令牌主体标识无效") from exc
        if user_id <= 0:
            raise auth_error.InvalidTokenError(detail="令牌主体标识无效")
        return user_id

    @staticmethod
    def _parse_auth_version(payload: dict[str, Any]) -> int:
        """解析认证版本声明。"""
        value = payload.get("auth_version")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise auth_error.InvalidTokenError(detail="令牌鉴权版本无效")
        try:
            auth_version = int(value)
        except (TypeError, ValueError) as exc:
            raise auth_error.InvalidTokenError(detail="令牌鉴权版本无效") from exc
        if auth_version < 0:
            raise auth_error.InvalidTokenError(detail="令牌鉴权版本无效")
        return auth_version

    @staticmethod
    def _parse_uuid(payload: dict[str, Any], key: str) -> UUID:
        """解析 UUID 声明。"""
        try:
            return UUID(str(payload[key]))
        except (KeyError, TypeError, ValueError) as exc:
            raise auth_error.InvalidTokenError(detail=f"令牌 {key} 声明无效") from exc
