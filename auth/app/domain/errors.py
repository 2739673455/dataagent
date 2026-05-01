"""领域错误 — 业务规则违例"""

from app.core.exceptions.base import (
    AuthError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)


class InvalidAccessTokenError(AuthError):
    type = "invalid-access-token"
    title = "访问令牌无效"


class InsufficientPermissionsError(PermissionDeniedError):
    type = "insufficient-permissions"
    title = "权限不足"


class InvalidGrantError(BadRequestError):
    type = "invalid-grant"
    title = "授权码无效"


class InvalidAuthorizationRequestError(BadRequestError):
    type = "invalid-authorization-request"
    title = "授权请求无效"


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


class RoleNotFoundError(NotFoundError):
    type = "role-not-found"
    title = "角色不存在"


class RoleAlreadyExistsError(ConflictError):
    type = "role-already-exists"
    title = "角色已存在"


class PermissionNotFoundError(NotFoundError):
    type = "permission-not-found"
    title = "权限不存在"


class PermissionAlreadyExistsError(ConflictError):
    type = "permission-already-exists"
    title = "权限已存在"
