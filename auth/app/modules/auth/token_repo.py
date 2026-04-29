"""访问令牌数据访问"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.datetime_str import future_str, now_str


class TokenRepo:
    """访问令牌数据访问实现"""

    async def create_token(
        self,
        db_session: AsyncSession,
        user_id: int,
        session_id: str,
        jti: str,
        expire_seconds: int,
        scopes: list[str],
    ) -> None:
        """创建访问令牌"""
        await db_session.execute(
            text(
                """
                INSERT INTO access_tokens
                    (jti, user_id, session_id, scopes_json, created_at, expires_at)
                VALUES
                    (:jti, :user_id, :session_id, :scopes_json, :created_at, :expires_at)
                """
            ),
            {
                "jti": jti,
                "user_id": user_id,
                "session_id": session_id,
                "scopes_json": json.dumps(scopes, ensure_ascii=False),
                "created_at": now_str(),
                "expires_at": future_str(expire_seconds),
            },
        )

    async def remove_token(
        self,
        db_session: AsyncSession,
        jti: str,
    ) -> None:
        """撤销指定的访问令牌"""
        await db_session.execute(
            text("DELETE FROM access_tokens WHERE jti = :jti"),
            {"jti": jti},
        )

    async def remove_all_tokens_by_user(
        self,
        db_session: AsyncSession,
        user_id: int,
    ) -> None:
        """撤销用户的所有有效访问令牌"""
        await db_session.execute(
            text("DELETE FROM access_tokens WHERE user_id = :user_id"),
            {"user_id": user_id},
        )

    async def remove_all_tokens_by_session(
        self,
        db_session: AsyncSession,
        session_id: str,
    ) -> None:
        """撤销会话下的所有有效访问令牌"""
        await db_session.execute(
            text("DELETE FROM access_tokens WHERE session_id = :session_id"),
            {"session_id": session_id},
        )

    async def update_all_tokens(
        self,
        db_session: AsyncSession,
        user_id: int,
        scopes: list[str],
    ) -> None:
        """更新用户所有有效令牌的权限"""
        await db_session.execute(
            text(
                """
                UPDATE access_tokens
                SET scopes_json = :scopes_json
                WHERE user_id = :user_id
                  AND expires_at > :now
                """
            ),
            {
                "user_id": user_id,
                "now": now_str(),
                "scopes_json": json.dumps(scopes, ensure_ascii=False),
            },
        )


token_repo = TokenRepo()
