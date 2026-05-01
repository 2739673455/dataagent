"""User HTTP 请求/响应 DTO"""

from pydantic import BaseModel, EmailStr, Field, field_validator


class SendCodeRequest(BaseModel):
    email: EmailStr = Field(..., description="邮箱")
    type: str = Field(
        default="register",
        description="验证码类型：register-注册, reset_email-重置邮箱, reset_password-重置密码",
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """校验验证码类型只能为预定义的三种值"""
        if v not in ("register", "reset_email", "reset_password"):
            raise ValueError(
                "Email code 类型只能为 register/reset_email/reset_password"
            )
        return v


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., description="邮箱")
    code: str = Field(..., description="邮箱验证码")
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """校验验证码：6位数字"""
        v = v.strip()
        if len(v) != 6:
            raise ValueError("验证码为6位数字")
        if not v.isdigit():
            raise ValueError("验证码只能包含数字")
        return v

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """校验用户名：不能包含 @，长度 1-50"""
        v = v.strip()
        if "@" in v:
            raise ValueError("用户名不能包含@字符")
        if len(v) < 1:
            raise ValueError("用户名不少于1个字符")
        if len(v) > 50:
            raise ValueError("用户名不超过50个字符")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """校验密码：长度 6-128"""
        if len(v) < 6:
            raise ValueError("密码不少于6个字符")
        if len(v) > 128:
            raise ValueError("密码不超过128个字符")
        return v


class UpdateUsernameRequest(BaseModel):
    username: str = Field(..., description="新用户名")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """校验用户名：不能包含 @，长度 1-50"""
        if "@" in v:
            raise ValueError("用户名不能包含@字符")
        if len(v) < 1:
            raise ValueError("用户名不少于1个字符")
        if len(v) > 50:
            raise ValueError("用户名不超过50个字符")
        return v.strip()


class UpdateEmailRequest(BaseModel):
    email: EmailStr = Field(..., description="新邮箱")
    code: str = Field(..., description="邮箱验证码")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """校验验证码：6位数字"""
        v = v.strip()
        if len(v) != 6:
            raise ValueError("验证码为6位数字")
        if not v.isdigit():
            raise ValueError("验证码只能包含数字")
        return v


class UpdatePasswordRequest(BaseModel):
    email: EmailStr = Field(..., description="邮箱")
    code: str = Field(..., description="邮箱验证码")
    password: str = Field(..., description="新密码")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """校验验证码：6位数字"""
        v = v.strip()
        if len(v) != 6:
            raise ValueError("验证码为6位数字")
        if not v.isdigit():
            raise ValueError("验证码只能包含数字")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """校验密码：长度 6-128"""
        if len(v) < 6:
            raise ValueError("密码不少于6个字符")
        if len(v) > 128:
            raise ValueError("密码不超过128个字符")
        return v


class UserResponse(BaseModel):
    username: str = Field(..., description="用户名")
    email: str = Field(..., description="邮箱")
    roles: list[str] = Field(..., description="用户角色")
