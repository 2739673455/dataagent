"""会话仓库 — SQLAlchemy 实现"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import Session
from app.domain.ports import SessionRepository
from app.utils.datetime_str import future_str, now_str


def _session_from_row(row) -> Session:
    """行记录 → Session 实体"""
    return Session(**dict(row))


class SessionRepo(SessionRepository):
    """会话仓库 SQLAlchemy 实现"""

    async def create(
        self, db: AsyncSession, session_id: str, user_id: int, expire_seconds: int
    ) -> None:
        """创建会话"""
        await db.execute(
            text(
                """INSERT INTO auth_session (session_id, user_id, created_at, expires_at)
                   VALUES (:sid, :uid, :now, :expires)"""
            ),
            {
                "sid": session_id,
                "uid": user_id,
                "now": now_str(),
                "expires": future_str(expire_seconds),
            },
        )

    async def remove(self, db: AsyncSession, session_id: str) -> None:
        """撤销会话（软删除）"""
        await db.execute(
            text(
                """UPDATE auth_session
                   SET revoked_at = COALESCE(revoked_at, :now)
                   WHERE session_id = :sid"""
            ),
            {"sid": session_id, "now": now_str()},
        )

    async def remove_all_by_user(self, db: AsyncSession, user_id: int) -> None:
        """撤销用户所有会话"""
        await db.execute(
            text(
                """UPDATE auth_session
                   SET revoked_at = COALESCE(revoked_at, :now)
                   WHERE user_id = :uid AND revoked_at IS NULL"""
            ),
            {"uid": user_id, "now": now_str()},
        )

    async def get_and_refresh(
        self, db: AsyncSession, session_id: str, expire_seconds: int
    ) -> Session | None:
        """获取有效会话并刷新过期时间（单条原子语句）"""
        result = await db.execute(
            text(
                """UPDATE auth_session
                   SET expires_at = :expires
                   WHERE session_id = :sid
                     AND expires_at > :now
                     AND revoked_at IS NULL
                   RETURNING session_id, user_id, created_at, expires_at, revoked_at"""
            ),
            {
                "sid": session_id,
                "expires": future_str(expire_seconds),
                "now": now_str(),
            },
        )
        row = result.mappings().first()
        return _session_from_row(row) if row else None
