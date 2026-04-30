"""会话数据访问"""

from app.entities.auth import Session
from app.utils.datetime_str import future_str, now_str
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _session_from_row(row) -> Session:
    return Session(**dict(row))


class SessionRepo:
    """会话数据访问实现"""

    async def create_session(
        self,
        db_session: AsyncSession,
        session_id: str,
        user_id: int,
        expires_seconds: int,
    ) -> None:
        """创建 Session"""
        await db_session.execute(
            text(
                """
                INSERT INTO auth_session
                    (session_id, user_id, created_at, expires_at)
                VALUES
                    (:session_id, :user_id, :created_at, :expires_at)
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "created_at": now_str(),
                "expires_at": future_str(expires_seconds),
            },
        )

    async def remove_session(self, db_session: AsyncSession, session_id: str) -> None:
        """撤销 Session"""
        await db_session.execute(
            text(
                """
                UPDATE auth_session
                SET revoked_at = COALESCE(revoked_at, :now)
                WHERE session_id = :session_id
                """
            ),
            {"session_id": session_id, "now": now_str()},
        )

    async def remove_all_sessions(
        self,
        db_session: AsyncSession,
        user_id: int,
    ) -> None:
        """撤销用户的所有 Session"""
        await db_session.execute(
            text(
                """
                UPDATE auth_session
                SET revoked_at = COALESCE(revoked_at, :now)
                WHERE user_id = :user_id
                  AND revoked_at IS NULL
                """
            ),
            {"user_id": user_id, "now": now_str()},
        )

    async def get_and_refresh_session(
        self,
        db_session: AsyncSession,
        session_id: str,
        expires_seconds: int,
    ) -> Session | None:
        """获取 Session 并刷新过期时间"""
        result = await db_session.execute(
            text(
                """
                SELECT session_id, user_id, created_at, expires_at, revoked_at
                FROM auth_session
                WHERE session_id = :session_id
                  AND expires_at > :now
                  AND revoked_at IS NULL
                """
            ),
            {"session_id": session_id, "now": now_str()},
        )
        row = result.mappings().first()
        if row is None:
            return None

        await db_session.execute(
            text(
                """
                UPDATE auth_session
                SET expires_at = :expires_at
                WHERE session_id = :session_id
                """
            ),
            {
                "session_id": session_id,
                "expires_at": future_str(expires_seconds),
            },
        )
        return _session_from_row(row)


session_repo = SessionRepo()
