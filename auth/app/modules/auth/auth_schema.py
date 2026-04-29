from pydantic import BaseModel, Field


class AccessTokenPayload(BaseModel):
    sub: int = Field(..., description="用户ID")
    exp: float = Field(..., description="过期时间戳")
    jti: str = Field(..., description="令牌唯一标识")
    scope: list[str] = Field(default=[], description="权限范围")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="访问令牌")
    token_type: str = Field(default="Bearer", description="令牌类型")


class IntrospectionResponse(BaseModel):
    active: bool = Field(..., description="令牌是否有效")
    sub: int | None = Field(default=None, description="用户标识")
    exp: float | None = Field(default=None, description="过期时间戳")
    scope: list[str] | None = Field(default=None, description="权限范围")
