from pydantic import BaseModel, Field


class AccessTokenPayload(BaseModel):
    sub: int = Field(..., description="用户ID")
    exp: float = Field(..., description="过期时间戳")
    scope: list[str] = Field(default=[], description="权限范围")


class IntrospectionResponse(BaseModel):
    active: bool = Field(..., description="令牌是否有效")
    sub: int | None = Field(default=None, description="用户标识")
    exp: float | None = Field(default=None, description="过期时间戳")
    scope: list[str] | None = Field(default=None, description="权限范围")

    def to_payload(self) -> AccessTokenPayload:
        if self.sub is None or self.exp is None:
            raise ValueError("Introspection response missing required payload fields")

        return AccessTokenPayload(
            sub=self.sub,
            exp=self.exp,
            scope=self.scope or [],
        )
