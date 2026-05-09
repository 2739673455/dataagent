from fastapi import status

from app.core.exceptions.base import AuthError, ProblemError


class MissingAccessTokenError(AuthError):
    type = "missing-access-token"
    title = "缺少访问令牌"
    status = status.HTTP_401_UNAUTHORIZED


class InvalidAccessTokenError(AuthError):
    type = "invalid-access-token"
    title = "访问令牌无效"
    status = status.HTTP_401_UNAUTHORIZED


class AuthServiceUnavailableError(ProblemError):
    type = "auth-service-unavailable"
    title = "认证服务不可用"
    status = status.HTTP_502_BAD_GATEWAY


class AuthServiceResponseError(ProblemError):
    type = "auth-service-response-error"
    title = "认证服务响应异常"
    status = status.HTTP_502_BAD_GATEWAY
