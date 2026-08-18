"""认证接口请求与响应模型"""

import re
from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.conf.app_config import cfg
from app.entities.auth import User
from app.services.auth_service import TokenPair

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class RegisterRequest(BaseModel):
    """用户注册请求"""

    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(
        min_length=cfg.auth.password_min_length,
        max_length=128,
    )

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        """校验并规范化邮箱"""
        normalized = value.strip().casefold()
        if not _EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("invalid email address")
        return normalized

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        """规范化用户名"""
        return value.strip().casefold()


class LoginRequest(BaseModel):
    """用户登录请求"""

    identifier: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """刷新令牌请求"""

    refresh_token: str = Field(min_length=1, max_length=8192)


class LogoutRequest(RefreshRequest):
    """退出登录请求"""


class UserResponse(BaseModel):
    """用户公开信息"""

    id: int
    username: str
    email: str
    is_active: bool
    is_admin: bool
    doris_role: str
    created_at: datetime

    @classmethod
    def from_entity(cls, user: User) -> Self:
        """从用户实体构造响应"""
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_admin=user.is_admin,
            doris_role=user.doris_role_name,
            created_at=user.created_at,
        )


class TokenResponse(BaseModel):
    """认证令牌响应"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int
    user: UserResponse

    @classmethod
    def from_result(cls, user: User, token_pair: TokenPair) -> Self:
        """从认证结果构造响应"""
        return cls(
            access_token=token_pair.access_token,
            refresh_token=token_pair.refresh_token,
            expires_in=token_pair.access_expires_in,
            refresh_expires_in=token_pair.refresh_expires_in,
            user=UserResponse.from_entity(user),
        )
