"""授权码数据访问"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.auth import AuthCode
from app.utils.datetime_str import future_str, now_str


def _auth_code_from_row(row) -> AuthCode:
    return AuthCode(**dict(row))


class AuthCodeRepo:
    """授权码数据访问实现"""

    async def create_auth_code(
        self,
        db_session: AsyncSession,
        code: str,
        user_id: int,
        session_id: str,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str,
        expire_seconds: int,
    ) -> None:
        """创建授权码"""
        await db_session.execute(
            text(
                """
                INSERT INTO authorization_code
                    (code, user_id, session_id, client_id, redirect_uri, state, code_challenge, code_challenge_method, created_at, expires_at)
                VALUES
                    (:code, :user_id, :session_id, :client_id, :redirect_uri, :state, :code_challenge, :code_challenge_method, :created_at, :expires_at)
                """
            ),
            {
                "code": code,
                "user_id": user_id,
                "session_id": session_id,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
                "created_at": now_str(),
                "expires_at": future_str(expire_seconds),
            },
        )

    async def get_active_auth_code(
        self,
        db_session: AsyncSession,
        code: str,
    ) -> AuthCode | None:
        """获取可用授权码"""
        result = await db_session.execute(
            text(
                """
                SELECT code, user_id, session_id, client_id, redirect_uri, state, code_challenge, code_challenge_method, created_at, expires_at, used_at
                FROM authorization_code
                WHERE code = :code
                  AND expires_at > :now
                  AND used_at IS NULL
                """
            ),
            {"code": code, "now": now_str()},
        )
        row = result.mappings().first()
        return _auth_code_from_row(row) if row else None

    async def mark_used(
        self,
        db_session: AsyncSession,
        code: str,
    ) -> None:
        """标记授权码已使用"""
        await db_session.execute(
            text(
                """
                UPDATE authorization_code
                SET used_at = :now
                WHERE code = :code
                """
            ),
            {"code": code, "now": now_str()},
        )


auth_code_repo = AuthCodeRepo()
