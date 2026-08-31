"""认证与授权异常。"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class UserAlreadyExistsError(ProblemError):
    """表示待创建的用户身份已存在。"""

    type = "user-already-exists"
    title = "用户已存在"
    status = HTTPStatus.CONFLICT


class UsernameAlreadyExistsError(UserAlreadyExistsError):
    """表示用户名已被其他用户占用。"""

    type = "username-already-exists"
    title = "用户名已存在"


class EmailAlreadyExistsError(UserAlreadyExistsError):
    """表示邮箱已被其他用户注册。"""

    type = "email-already-exists"
    title = "邮箱已注册"


class WeakPasswordError(ProblemError):
    """表示密码未满足安全策略。"""

    type = "weak-password"
    title = "密码强度不足"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class InvalidUserMutationError(ProblemError):
    """表示用户资料变更违反业务约束。"""

    type = "invalid-user-mutation"
    title = "用户操作无效"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class InvalidCredentialsError(ProblemError):
    """表示登录凭据校验失败。"""

    type = "invalid-credentials"
    title = "用户名或密码错误"
    status = HTTPStatus.UNAUTHORIZED


class InvalidCurrentPasswordError(ProblemError):
    """表示当前密码校验失败。"""

    type = "invalid-current-password"
    title = "当前密码错误"
    status = HTTPStatus.UNAUTHORIZED


class AuthenticationRequiredError(ProblemError):
    """表示当前请求需要有效登录身份。"""

    type = "authentication-required"
    title = "需要登录"
    status = HTTPStatus.UNAUTHORIZED


class InvalidTokenError(ProblemError):
    """表示认证令牌无效或已经过期。"""

    type = "invalid-token"
    title = "令牌无效或已过期"
    status = HTTPStatus.UNAUTHORIZED


class RefreshTokenReuseError(ProblemError):
    """表示检测到刷新令牌重复使用。"""

    type = "refresh-token-reuse"
    title = "检测到刷新令牌重放"
    status = HTTPStatus.UNAUTHORIZED


class InactiveUserError(ProblemError):
    """表示目标用户已被停用。"""

    type = "inactive-user"
    title = "用户已停用"
    status = HTTPStatus.FORBIDDEN


class PermissionDeniedError(ProblemError):
    """表示当前身份缺少所需权限。"""

    type = "permission-denied"
    title = "权限不足"
    status = HTTPStatus.FORBIDDEN


class UserNotFoundError(ProblemError):
    """表示目标用户不存在。"""

    type = "user-not-found"
    title = "用户不存在"
    status = HTTPStatus.NOT_FOUND


class RoleNotFoundError(ProblemError):
    """表示目标 Doris 角色不存在。"""

    type = "role-not-found"
    title = "角色不存在"
    status = HTTPStatus.NOT_FOUND


class RoleAlreadyExistsError(ProblemError):
    """表示目标 Doris 角色已经存在。"""

    type = "role-already-exists"
    title = "角色已存在"
    status = HTTPStatus.CONFLICT


class QueryUserAlreadyExistsError(ProblemError):
    """表示 Doris 查询用户名已经被占用。"""

    type = "query-user-already-exists"
    title = "查询用户已存在"
    status = HTTPStatus.CONFLICT


class WorkloadGroupNotFoundError(ProblemError):
    """表示目标 Doris 工作组不存在。"""

    type = "workload-group-not-found"
    title = "Doris 工作组不存在"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class RoleInUseError(ProblemError):
    """表示 Doris 角色仍被平台用户引用。"""

    type = "role-in-use"
    title = "角色仍被用户使用"
    status = HTTPStatus.CONFLICT


class AssetGrantNotFoundError(ProblemError):
    """表示目标数据资产授权不存在。"""

    type = "asset-grant-not-found"
    title = "资产授权不存在"
    status = HTTPStatus.NOT_FOUND


class AssetGrantAlreadyExistsError(ProblemError):
    """表示目标数据资产授权已经存在。"""

    type = "asset-grant-already-exists"
    title = "资产授权已存在"
    status = HTTPStatus.CONFLICT


class LastAdministratorError(ProblemError):
    """表示操作会移除最后一位平台管理员。"""

    type = "last-administrator"
    title = "必须保留至少一位管理员"
    status = HTTPStatus.CONFLICT


class InvalidDorisPermissionError(ProblemError):
    """表示 Doris 权限配置不符合查询身份约束。"""

    type = "invalid-doris-permission"
    title = "Doris 权限配置无效"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class RateLimitExceededError(ProblemError):
    """表示认证请求超过允许频率。"""

    type = "rate-limit-exceeded"
    title = "请求过于频繁"
    status = HTTPStatus.TOO_MANY_REQUESTS

    def __init__(
        self,
        *,
        retry_after_seconds: int,
        detail: str | None = None,
    ) -> None:
        """初始化包含建议重试时间的限流错误。"""
        super().__init__(
            detail=detail or "认证请求过于频繁，请稍后重试",
            extensions={"retry_after_seconds": retry_after_seconds},
        )
