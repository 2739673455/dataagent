"""授权码仓库 — SQLAlchemy 实现"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import AuthCode
from app.domain.ports import AuthCodeRepository
from app.utils.datetime_str import future_str, now_str


def _auth_code_from_row(row) -> AuthCode:
    """行记录 → AuthCode 实体"""
    return AuthCode(**dict(row))


class AuthCodeRepo(AuthCodeRepository):
    """授权码仓库 SQLAlchemy 实现"""

    async def create(
        self,
        db: AsyncSession,
        *,
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
        await db.execute(
            text(
                """INSERT INTO authorization_code
                   (code, user_id, session_id, client_id, redirect_uri, state,
                    code_challenge, code_challenge_method, created_at, expires_at)
                   VALUES (:code, :user_id, :session_id, :client_id, :redirect_uri, :state,
                           :code_challenge, :code_challenge_method, :now, :expires)"""
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
                "now": now_str(),
                "expires": future_str(expire_seconds),
            },
        )

    async def get_active(self, db: AsyncSession, code: str) -> AuthCode | None:
        """获取未过期且未使用的授权码"""
        result = await db.execute(
            text(
                """SELECT code, user_id, session_id, client_id, redirect_uri, state,
                          code_challenge, code_challenge_method, created_at, expires_at, used_at
                   FROM authorization_code
                   WHERE code = :code
                     AND expires_at > :now
                     AND used_at IS NULL"""
            ),
            {"code": code, "now": now_str()},
        )
        row = result.mappings().first()
        return _auth_code_from_row(row) if row else None

    async def mark_used(self, db: AsyncSession, code: str) -> None:
        """将授权码标记为已使用"""
        await db.execute(
            text("UPDATE authorization_code SET used_at = :now WHERE code = :code"),
            {"code": code, "now": now_str()},
        )
