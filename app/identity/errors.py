"""用户选择与 Doris 查询身份异常。"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class UserSelectionRequiredError(ProblemError):
    """请求未选择用户或所选用户不存在。"""

    type = "user-selection-required"
    title = "未选择有效用户"
    status = HTTPStatus.UNAUTHORIZED


class InvalidDorisPermissionError(ProblemError):
    """授权结果缺少有效字段，或查询账号未绑定配置角色。"""

    type = "invalid-doris-permission"
    title = "Doris 权限配置无效"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class QueryPrincipalNotConfiguredError(RuntimeError):
    """用户绑定的角色缺少查询身份。"""


class DorisCredentialError(RuntimeError):
    """Doris 查询凭据无法解密。"""
