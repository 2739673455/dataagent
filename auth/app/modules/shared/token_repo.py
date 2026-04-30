import json

from app.entities.auth import AccessToken
from app.utils.datetime_str import future_str, now_str
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _token_from_row(row) -> AccessToken:
    return AccessToken(**dict(row))


class TokenRepo:
    """访问令牌数据访问实现"""

    async def create_token(
        self,
        db_session: AsyncSession,
        user_id: int,
        session_id: str,
        access_token: str,
        client_id: str,
        expire_seconds: int,
        scopes: list[str],
    ) -> None:
        """创建访问令牌"""
        await db_session.execute(
            text(
                """
                INSERT INTO access_token
                    (access_token, user_id, session_id, client_id, scope, created_at, expires_at)
                VALUES
                    (:access_token, :user_id, :session_id, :client_id, :scope, :created_at, :expires_at)
                """
            ),
            {
                "access_token": access_token,
                "user_id": user_id,
                "session_id": session_id,
                "client_id": client_id,
                "scope": json.dumps(scopes, ensure_ascii=False),
                "created_at": now_str(),
                "expires_at": future_str(expire_seconds),
            },
        )

    async def remove_token(
        self,
        db_session: AsyncSession,
        access_token: str,
    ) -> None:
        """撤销指定的访问令牌"""
        await db_session.execute(
            text(
                """
                UPDATE access_token
                SET revoked_at = COALESCE(revoked_at, :now)
                WHERE access_token = :access_token
                """
            ),
            {"access_token": access_token, "now": now_str()},
        )

    async def remove_all_tokens_by_user(
        self,
        db_session: AsyncSession,
        user_id: int,
    ) -> None:
        """撤销用户的所有有效访问令牌"""
        await db_session.execute(
            text(
                """
                UPDATE access_token
                SET revoked_at = COALESCE(revoked_at, :now)
                WHERE user_id = :user_id
                  AND revoked_at IS NULL
                """
            ),
            {"user_id": user_id, "now": now_str()},
        )

    async def remove_all_tokens_by_session(
        self,
        db_session: AsyncSession,
        session_id: str,
    ) -> None:
        """撤销会话下的所有有效访问令牌"""
        await db_session.execute(
            text(
                """
                UPDATE access_token
                SET revoked_at = COALESCE(revoked_at, :now)
                WHERE session_id = :session_id
                  AND revoked_at IS NULL
                """
            ),
            {"session_id": session_id, "now": now_str()},
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
                UPDATE access_token
                SET scope = :scope
                WHERE user_id = :user_id
                  AND expires_at > :now
                  AND revoked_at IS NULL
                """
            ),
            {
                "user_id": user_id,
                "now": now_str(),
                "scope": json.dumps(scopes, ensure_ascii=False),
            },
        )


token_repo = TokenRepo()
