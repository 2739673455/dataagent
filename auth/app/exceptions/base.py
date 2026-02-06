from typing import Any

from fastapi import status


class AppError(Exception):
    code: int = 1000
    message: str = "服务器内部错误"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: Any = None,
        code: int | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.detail = detail
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class InternalServerError(AppError):
    code = 1000
    message = "服务器内部错误"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


class ValidationError(AppError):
    code = 1100
    message = "参数校验失败"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class AuthError(AppError):
    code = 1200
    message = "认证失败"
    status_code = status.HTTP_401_UNAUTHORIZED


class PermissionDeniedError(AppError):
    code = 1300
    message = "权限不足"
    status_code = status.HTTP_403_FORBIDDEN


class NotFoundError(AppError):
    code = 1400
    message = "资源不存在"
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    code = 1500
    message = "资源冲突"
    status_code = status.HTTP_409_CONFLICT


class BadRequestError(AppError):
    code = 1600
    message = "请求参数错误"
    status_code = status.HTTP_400_BAD_REQUEST
