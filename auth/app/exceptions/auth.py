"""认证异常"""

from app.exceptions.base import AuthError, PermissionDeniedError


class InvalidAccessTokenError(AuthError):
    code = 1201
    message = "无效访问令牌"


class ExpiredAccessTokenError(AuthError):
    code = 1202
    message = "访问令牌过期"


class InvalidRefreshTokenError(AuthError):
    code = 1203
    message = "无效刷新令牌"


class ExpiredRefreshTokenError(AuthError):
    code = 1204
    message = "刷新令牌过期"


class InsufficientPermissionsError(PermissionDeniedError):
    code = 1301
    message = "权限不足"
