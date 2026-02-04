from pydantic import BaseModel, EmailStr, Field, field_validator


class AccessTokenPayload(BaseModel):
    sub: int
    scope: list[str]
    exp: float
    jti: str


class RefreshTokenPayload(BaseModel):
    sub: int
    scope: list[str]
    exp: float
    jti: str


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., description="邮箱")
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1:
            raise ValueError("用户名不少于1个字符")
        if len(v) > 50:
            raise ValueError("用户名不超过50个字符")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("密码不少于6个字符")
        if len(v) > 128:
            raise ValueError("密码不超过128个字符")
        return v


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="邮箱")
    password: str = Field(..., description="密码")


class UpdateUsernameRequest(BaseModel):
    username: str = Field(..., description="新用户名")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if len(v) < 1:
            raise ValueError("用户名不少于1个字符")
        if len(v) > 50:
            raise ValueError("用户名不超过50个字符")
        return v.strip()


class UpdateEmailRequest(BaseModel):
    email: EmailStr = Field(..., description="新邮箱")


class UpdatePasswordRequest(BaseModel):
    password: str = Field(..., description="新密码")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("密码不少于6个字符")
        if len(v) > 128:
            raise ValueError("密码不超过128个字符")
        return v


class UserResponse(BaseModel):
    username: str
    email: str
    groups: list[str]


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
