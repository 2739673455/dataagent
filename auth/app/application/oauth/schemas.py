"""OAuth 模块应用层 DTO"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthorizeResult:
    """授权端点返回：授权码 + 重定向信息"""

    code: str
    redirect_uri: str
    session_id: str
    session_expire_seconds: int
    state: str


@dataclass(frozen=True, slots=True)
class TokenResult:
    """令牌端点返回：access token"""

    access_token: str
