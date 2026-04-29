"""用户异常"""

from app.core.exceptions.base import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)


class EmailAlreadyExistsError(ConflictError):
    type = "email-already-exists"
    title = "邮箱已被注册"


class EmailNotFoundError(NotFoundError):
    type = "email-not-found"
    title = "邮箱不存在"


class UserNotFoundError(NotFoundError):
    type = "user-not-found"
    title = "用户不存在"


class UserDisabledError(PermissionDeniedError):
    type = "user-disabled"
    title = "用户已被禁用"


class InvalidCredentialsError(BadRequestError):
    type = "invalid-credentials"
    title = "邮箱或密码错误"


class UsernameUnchangedError(BadRequestError):
    type = "username-unchanged"
    title = "用户名未改变"


class EmailUnchangedError(BadRequestError):
    type = "email-unchanged"
    title = "邮箱未改变"


class InvalidVerificationCodeError(BadRequestError):
    type = "invalid-verification-code"
    title = "验证码错误或已过期"
