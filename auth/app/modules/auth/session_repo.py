"""会话数据访问"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.auth import Sessions
from app.utils.datetime_str import future_str, now_str


def _session_from_row(row) -> Sessions:
    return Sessions(**dict(row))


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
        current_time = now_str()
        await db_session.execute(
            text(
                """
                INSERT INTO sessions
                    (session_id, user_id, created_at, updated_at, expires_at)
                VALUES
                    (:session_id, :user_id, :created_at, :updated_at, :expires_at)
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "created_at": current_time,
                "updated_at": current_time,
                "expires_at": future_str(expires_seconds),
            },
        )

    async def remove_session(self, db_session: AsyncSession, session_id: str) -> None:
        """删除 Session"""
        await db_session.execute(
            text("DELETE FROM sessions WHERE session_id = :session_id"),
            {"session_id": session_id},
        )

    async def remove_all_sessions(
        self,
        db_session: AsyncSession,
        user_id: int,
    ) -> None:
        """删除用户的所有 Session"""
        await db_session.execute(
            text("DELETE FROM sessions WHERE user_id = :user_id"),
            {"user_id": user_id},
        )

    async def get_and_refresh_session(
        self,
        db_session: AsyncSession,
        session_id: str,
        expires_seconds: int,
    ) -> Sessions | None:
        """获取 Session 并刷新过期时间"""
        result = await db_session.execute(
            text(
                """
                SELECT session_id, user_id, created_at, updated_at, expires_at
                FROM sessions
                WHERE session_id = :session_id
                  AND expires_at > :now
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
                UPDATE sessions
                SET expires_at = :expires_at,
                    updated_at = :updated_at
                WHERE session_id = :session_id
                """
            ),
            {
                "session_id": session_id,
                "expires_at": future_str(expires_seconds),
                "updated_at": now_str(),
            },
        )
        return _session_from_row(row)


session_repo = SessionRepo()
