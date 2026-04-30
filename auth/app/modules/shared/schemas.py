"""共享数据模型"""

from pydantic import BaseModel, Field


class AccessTokenPayload(BaseModel):
    access_token: str = Field(..., description="访问令牌")
    sub: int = Field(..., description="用户ID")
    exp: float = Field(..., description="过期时间戳")
    scope: list[str] = Field(default=[], description="权限范围")


class SessionCookieData(BaseModel):
    session_id: str
    session_expire_seconds: int
