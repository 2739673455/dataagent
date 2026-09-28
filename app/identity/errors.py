"""用户选择与 Doris 查询身份异常。"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class UserSelectionRequiredError(ProblemError):
    """请求未选择用户或所选用户不存在。"""

    type = "user-selection-required"
    title = "请选择用户"
    status = HTTPStatus.UNAUTHORIZED


class UserNotFoundError(ProblemError):
    """表示目标用户不存在。"""

    type = "user-not-found"
    title = "用户不存在"
    status = HTTPStatus.NOT_FOUND


class InvalidDorisPermissionError(ProblemError):
    """Doris 权限结果无法解析，或不符合业务查询身份约束。"""

    type = "invalid-doris-permission"
    title = "Doris 权限配置无效"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class DorisCredentialError(RuntimeError):
    """Doris 查询凭据无法解密。"""


class QueryPrincipalNotConfiguredError(RuntimeError):
    """用户绑定的角色缺少查询身份。"""
