"""权限相关异常"""

from app.core.exceptions.base import ConflictError, NotFoundError


class PermissionNotFoundError(NotFoundError):
    type = "permission-not-found"
    title = "权限不存在"


class PermissionAlreadyExistsError(ConflictError):
    type = "permission-already-exists"
    title = "权限已存在"
