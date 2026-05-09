"""邮箱验证码仓库 — SQLAlchemy 实现"""

from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ports import EmailCodeRepository
from app.utils.datetime_str import future_str, now_str


class EmailCodeRepo(EmailCodeRepository):
    """邮箱验证码仓库 SQLAlchemy 实现"""

    async def create(
        self,
        db: AsyncSession,
        email: str,
        code_type: str,
        code: str,
        expire_seconds: int,
    ) -> None:
        """创建验证码（先删除同类型旧验证码）"""
        await db.execute(
            text("DELETE FROM email_code WHERE email = :email AND code_type = :type"),
            {"email": email, "type": code_type},
        )
        await db.execute(
            text(
                """INSERT INTO email_code (email, code_type, code, created_at, expires_at)
                   VALUES (:email, :type, :code, :now, :expires)"""
            ),
            {
                "email": email,
                "type": code_type,
                "code": code,
                "now": now_str(),
                "expires": future_str(expire_seconds),
            },
        )

    async def consume(self, db: AsyncSession, email: str, code_type: str, code: str) -> bool:
        """消费验证码（标记为已使用），返回是否消费成功"""
        result = await db.execute(
            text(
                """UPDATE email_code
                   SET used_at = :now
                   WHERE email = :email
                     AND code_type = :type
                     AND code = :code
                     AND expires_at > :now
                     AND used_at IS NULL"""
            ),
            {"email": email, "type": code_type, "code": code, "now": now_str()},
        )
        return cast(CursorResult[Any], result).rowcount == 1
