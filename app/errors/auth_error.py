"""认证与授权异常"""

from http import HTTPStatus

from app.errors.base import ProblemError


class UserAlreadyExistsError(ProblemError):
    type = "user-already-exists"
    title = "用户已存在"
    status = HTTPStatus.CONFLICT


class UsernameAlreadyExistsError(UserAlreadyExistsError):
    type = "username-already-exists"
    title = "用户名已存在"


class EmailAlreadyExistsError(UserAlreadyExistsError):
    type = "email-already-exists"
    title = "邮箱已注册"


class WeakPasswordError(ProblemError):
    type = "weak-password"
    title = "密码强度不足"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class InvalidUserMutationError(ProblemError):
    type = "invalid-user-mutation"
    title = "用户操作无效"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class InvalidCredentialsError(ProblemError):
    type = "invalid-credentials"
    title = "用户名或密码错误"
    status = HTTPStatus.UNAUTHORIZED


class AuthenticationRequiredError(ProblemError):
    type = "authentication-required"
    title = "需要登录"
    status = HTTPStatus.UNAUTHORIZED


class InvalidTokenError(ProblemError):
    type = "invalid-token"
    title = "令牌无效或已过期"
    status = HTTPStatus.UNAUTHORIZED


class RefreshTokenReuseError(ProblemError):
    type = "refresh-token-reuse"
    title = "检测到刷新令牌重放"
    status = HTTPStatus.UNAUTHORIZED


class InactiveUserError(ProblemError):
    type = "inactive-user"
    title = "用户已停用"
    status = HTTPStatus.FORBIDDEN


class PermissionDeniedError(ProblemError):
    type = "permission-denied"
    title = "权限不足"
    status = HTTPStatus.FORBIDDEN


class AssetAccessDeniedError(PermissionDeniedError):
    type = "asset-access-denied"
    title = "无权访问该数据资产"


class UserNotFoundError(ProblemError):
    type = "user-not-found"
    title = "用户不存在"
    status = HTTPStatus.NOT_FOUND


class RoleNotFoundError(ProblemError):
    type = "role-not-found"
    title = "角色不存在"
    status = HTTPStatus.NOT_FOUND


class RoleAlreadyExistsError(ProblemError):
    type = "role-already-exists"
    title = "角色已存在"
    status = HTTPStatus.CONFLICT


class RoleInUseError(ProblemError):
    type = "role-in-use"
    title = "角色仍被用户使用"
    status = HTTPStatus.CONFLICT


class DefaultRoleRequiredError(ProblemError):
    type = "default-role-required"
    title = "必须保留一个启用的缺省 Doris 角色"
    status = HTTPStatus.CONFLICT


class AssetGrantNotFoundError(ProblemError):
    type = "asset-grant-not-found"
    title = "资产授权不存在"
    status = HTTPStatus.NOT_FOUND


class AssetGrantAlreadyExistsError(ProblemError):
    type = "asset-grant-already-exists"
    title = "资产授权已存在"
    status = HTTPStatus.CONFLICT


class LastAdministratorError(ProblemError):
    type = "last-administrator"
    title = "必须保留至少一位管理员"
    status = HTTPStatus.CONFLICT


class InvalidDorisPermissionError(ProblemError):
    type = "invalid-doris-permission"
    title = "Doris 权限配置无效"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class RateLimitExceededError(ProblemError):
    type = "rate-limit-exceeded"
    title = "请求过于频繁"
    status = HTTPStatus.TOO_MANY_REQUESTS

    def __init__(
        self,
        *,
        retry_after_seconds: int,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            detail=detail or "Too many authentication attempts",
            extensions={"retry_after_seconds": retry_after_seconds},
        )
