"""User 模块应用层 DTO"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserInfoResult:
    """当前用户基本信息"""

    username: str
    email: str
    roles: list[str]
