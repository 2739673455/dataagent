"""角色相关异常"""

from app.core.exceptions.base import ConflictError, NotFoundError


class RoleNotFoundError(NotFoundError):
    type = "role-not-found"
    title = "角色不存在"


class RoleAlreadyExistsError(ConflictError):
    type = "role-already-exists"
    title = "角色已存在"
