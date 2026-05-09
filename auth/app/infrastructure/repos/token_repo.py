"""令牌仓库 — SQLAlchemy 实现"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import AccessToken
from app.domain.ports import TokenRepository
from app.utils.datetime_str import future_str, now_str


def _token_from_row(row) -> AccessToken:
    """行记录 → AccessToken 实体"""
    return AccessToken(**dict(row))


class TokenRepo(TokenRepository):
    """令牌仓库 SQLAlchemy 实现"""

    async def create(
        self,
        db: AsyncSession,
        user_id: int,
        session_id: str,
        access_token: str,
        client_id: str,
        expire_seconds: int,
        scopes: list[str],
    ) -> None:
        """创建访问令牌"""
        await db.execute(
            text(
                """INSERT INTO access_token
                   (access_token, user_id, session_id, client_id, scope, created_at, expires_at)
                   VALUES (:token, :user_id, :session_id, :client_id, :scope, :now, :expires)"""
            ),
            {
                "token": access_token,
                "user_id": user_id,
                "session_id": session_id,
                "client_id": client_id,
                "scope": json.dumps(scopes, ensure_ascii=False),
                "now": now_str(),
                "expires": future_str(expire_seconds),
            },
        )

    async def remove(self, db: AsyncSession, access_token: str) -> None:
        """撤销单个令牌（软删除）"""
        await db.execute(
            text(
                """UPDATE access_token
                   SET revoked_at = COALESCE(revoked_at, :now)
                   WHERE access_token = :token"""
            ),
            {"token": access_token, "now": now_str()},
        )

    async def remove_all_by_user(self, db: AsyncSession, user_id: int) -> None:
        """撤销用户所有令牌"""
        await db.execute(
            text(
                """UPDATE access_token
                   SET revoked_at = COALESCE(revoked_at, :now)
                   WHERE user_id = :user_id AND revoked_at IS NULL"""
            ),
            {"user_id": user_id, "now": now_str()},
        )

    async def remove_all_by_session(self, db: AsyncSession, session_id: str) -> None:
        """撤销会话下所有令牌"""
        await db.execute(
            text(
                """UPDATE access_token
                   SET revoked_at = COALESCE(revoked_at, :now)
                   WHERE session_id = :session_id AND revoked_at IS NULL"""
            ),
            {"session_id": session_id, "now": now_str()},
        )

    async def update_all_by_user(self, db: AsyncSession, user_id: int, scopes: list[str]) -> None:
        """更新用户所有令牌的权限范围"""
        await db.execute(
            text(
                """UPDATE access_token
                   SET scope = :scope
                   WHERE user_id = :user_id AND revoked_at IS NULL"""
            ),
            {"user_id": user_id, "scope": json.dumps(scopes, ensure_ascii=False)},
        )

    async def get_active(self, db: AsyncSession, access_token: str) -> AccessToken | None:
        """获取有效的令牌"""
        result = await db.execute(
            text(
                """SELECT access_token, user_id, session_id, client_id, scope,
                          created_at, expires_at, revoked_at
                   FROM access_token
                   WHERE access_token = :token
                     AND expires_at > :now
                     AND revoked_at IS NULL
                   LIMIT 1"""
            ),
            {"token": access_token, "now": now_str()},
        )
        row = result.mappings().first()
        return _token_from_row(row) if row else None
