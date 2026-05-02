from pydantic import BaseModel, Field


class IntrospectionResponse(BaseModel):
    active: bool = Field(..., description="令牌是否有效")
    sub: int | None = Field(default=None, description="用户标识")
    exp: float | None = Field(default=None, description="过期时间戳")
    scope: list[str] | None = Field(default=None, description="权限范围")
