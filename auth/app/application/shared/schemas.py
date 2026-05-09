"""应用层共享 DTO"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionCookieResult:
    """会话 Cookie 数据"""

    session_id: str
    session_expire_seconds: int
