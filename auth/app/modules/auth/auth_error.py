"""认证异常"""

from app.core.exceptions.base import AuthError, BadRequestError, PermissionDeniedError


class InvalidAccessTokenError(AuthError):
    type = "invalid-access-token"
    title = "访问令牌无效"


class InsufficientPermissionsError(PermissionDeniedError):
    type = "insufficient-permissions"
    title = "权限不足"


class InvalidGrantError(BadRequestError):
    type = "invalid-grant"
    title = "授权码无效"
