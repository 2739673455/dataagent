"""用户选择与 Doris 查询身份异常。"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class UserSelectionRequiredError(ProblemError):
    """请求未选择用户或所选用户不存在。"""

    type = "user-selection-required"
    title = "未选择有效用户"
    status = HTTPStatus.UNAUTHORIZED


class QueryPrincipalNotConfiguredError(RuntimeError):
    """用户绑定的角色缺少查询身份。"""


class DorisCredentialError(RuntimeError):
    """Doris 查询凭据无法解密。"""
