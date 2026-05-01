"""创建用户会话的共享领域服务"""

from app.core.settings import AuthCfg
from app.domain.ports import SessionRepository, TokenFactory

from .schemas import SessionCookieResult


class SessionCreator:
    """创建用户会话的共享服务 — 生成 session_id 并持久化"""

    def __init__(
        self,
        session_repo: SessionRepository,
        auth_config: AuthCfg,
        token_factory: TokenFactory,
    ) -> None:
        self._repo = session_repo
        self._cfg = auth_config
        self._tf = token_factory

    async def create(self, db, user_id: int) -> SessionCookieResult:
        """创建会话并返回 session_id 与过期秒数"""
        sid = self._tf.session_id()
        expire = self._cfg.session_expire_days * 24 * 60 * 60
        await self._repo.create(db, sid, user_id, expire)
        return SessionCookieResult(session_id=sid, session_expire_seconds=expire)
