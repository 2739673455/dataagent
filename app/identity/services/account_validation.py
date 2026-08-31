"""账号字段规范化与校验规则。"""

import re

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 64
EMAIL_MAX_LENGTH = 320
PASSWORD_MAX_LENGTH = 128

_USERNAME_PATTERN = re.compile(
    rf"^[a-z0-9_.-]{{{USERNAME_MIN_LENGTH},{USERNAME_MAX_LENGTH}}}$"
)
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_username(username: str) -> str:
    """返回规范化用户名并校验允许字符。"""
    normalized = username.strip().casefold()
    if _USERNAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError("用户名只能包含小写字母、数字、点、下划线和连字符")
    return normalized


def validate_email(email: str) -> str:
    """返回规范化邮箱并校验基本结构。"""
    normalized = email.strip().casefold()
    if (
        len(normalized) > EMAIL_MAX_LENGTH
        or _EMAIL_PATTERN.fullmatch(normalized) is None
    ):
        raise ValueError("邮箱地址格式无效")
    return normalized


def validate_password_length(password: str, *, min_length: int) -> None:
    """按运行时策略校验新密码长度。"""
    if len(password) < min_length:
        raise ValueError(f"密码长度不能少于 {min_length} 位")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"密码长度不能超过 {PASSWORD_MAX_LENGTH} 位")
