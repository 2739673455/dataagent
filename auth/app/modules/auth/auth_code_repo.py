"""授权码数据访问"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.auth import AuthCodes
from app.utils.datetime_str import future_str, now_str


def _auth_code_from_row(row) -> AuthCodes:
    return AuthCodes(**dict(row))


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
        expire_seconds: int,
    ) -> None:
        """创建授权码"""
        await db_session.execute(
            text(
                """
                INSERT INTO auth_codes
                    (code, user_id, session_id, client_id, redirect_uri, created_at, expires_at)
                VALUES
                    (:code, :user_id, :session_id, :client_id, :redirect_uri, :created_at, :expires_at)
                """
            ),
            {
                "code": code,
                "user_id": user_id,
                "session_id": session_id,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "created_at": now_str(),
                "expires_at": future_str(expire_seconds),
            },
        )

    async def consume_auth_code(
        self,
        db_session: AsyncSession,
        code: str,
    ) -> AuthCodes | None:
        """消费授权码"""
        result = await db_session.execute(
            text(
                """
                SELECT code, user_id, session_id, client_id, redirect_uri, created_at, expires_at
                FROM auth_codes
                WHERE code = :code
                  AND expires_at > :now
                """
            ),
            {"code": code, "now": now_str()},
        )
        row = result.mappings().first()
        if row is None:
            return None

        await db_session.execute(
            text("DELETE FROM auth_codes WHERE code = :code"),
            {"code": code},
        )
        return _auth_code_from_row(row)


auth_code_repo = AuthCodeRepo()
