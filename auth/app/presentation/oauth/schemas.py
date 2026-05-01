"""OAuth HTTP 请求/响应 DTO"""

from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="邮箱")
    password: str = Field(..., description="密码")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """校验密码：长度 6-128"""
        if len(v) < 6:
            raise ValueError("密码不少于6个字符")
        if len(v) > 128:
            raise ValueError("密码不超过128个字符")
        return v


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="访问令牌")
    token_type: str = Field(default="Bearer", description="令牌类型")


class IntrospectionResponse(BaseModel):
    active: bool = Field(..., description="令牌是否有效")
    sub: int | None = Field(default=None, description="用户标识")
    exp: float | None = Field(default=None, description="过期时间戳")
    scope: list[str] | None = Field(default=None, description="权限范围")
