from fastapi import status

from app.exceptions.base import AppError, AuthError


class MissingAccessTokenError(AuthError):
    code = 1201
    message = "缺少访问令牌"


class InvalidAccessTokenError(AuthError):
    code = 1202
    message = "访问令牌无效"


class AuthServiceUnavailableError(AppError):
    code = 1203
    message = "认证服务不可用"
    status_code = status.HTTP_502_BAD_GATEWAY


class AuthServiceResponseError(AppError):
    code = 1204
    message = "认证服务响应异常"
    status_code = status.HTTP_502_BAD_GATEWAY
