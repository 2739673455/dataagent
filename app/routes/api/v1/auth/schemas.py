"""认证接口请求与响应模型"""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, SecretStr

from app.models.auth import User
from app.services.auth_service import TokenPair


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
    doris_role: str | None
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
