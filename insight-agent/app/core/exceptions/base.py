"""应用层异常基类 — RFC 9457 Problem Details 风格"""

from typing import Any

from fastapi import status as http_status


class ProblemError(Exception):
    """异常基类，可转换为结构化错误响应"""

    type: str = "internal-server-error"
    title: str = "服务器内部错误"
    status: int = http_status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(
        self,
        title: str | None = None,
        *,
        detail: str | None = None,
        type: str | None = None,
        status: int | None = None,
    ) -> None:
        self.title = title or self.title
        self.detail = detail
        if type is not None:
            self.type = type
        if status is not None:
            self.status = status

        super().__init__(self.title)

    def to_problem(
        self,
        *,
        instance: str | None = None,
    ) -> dict[str, Any]:
        """转换为响应体"""
        payload: dict[str, Any] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
        }

        if self.detail is not None:
            payload["detail"] = self.detail
        if instance:
            payload["instance"] = instance

        return payload


class InternalServerError(ProblemError):
    type = "internal-server-error"
    title = "服务器内部错误"
    status = http_status.HTTP_500_INTERNAL_SERVER_ERROR


class ValidationError(ProblemError):
    type = "validation-error"
    title = "参数校验失败"
    status = http_status.HTTP_422_UNPROCESSABLE_CONTENT


class AuthError(ProblemError):
    type = "authentication-failed"
    title = "认证失败"
    status = http_status.HTTP_401_UNAUTHORIZED


class PermissionDeniedError(ProblemError):
    type = "permission-denied"
    title = "权限不足"
    status = http_status.HTTP_403_FORBIDDEN


class NotFoundError(ProblemError):
    type = "not-found"
    title = "资源不存在"
    status = http_status.HTTP_404_NOT_FOUND


class ConflictError(ProblemError):
    type = "conflict"
    title = "资源冲突"
    status = http_status.HTTP_409_CONFLICT


class BadRequestError(ProblemError):
    type = "bad-request"
    title = "请求参数错误"
    status = http_status.HTTP_400_BAD_REQUEST
