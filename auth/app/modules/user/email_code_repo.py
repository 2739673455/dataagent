"""邮箱验证码数据访问"""

from typing import Any, cast

from app.utils.datetime_str import future_str, now_str
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession


class EmailCodeRepo:
    async def create(
        self,
        db_session: AsyncSession,
        email: str,
        code_type: str,
        code: str,
        expire_seconds: int,
    ) -> None:
        """创建验证码"""
        await db_session.execute(
            text(
                """
                DELETE FROM email_code
                WHERE email = :email
                  AND code_type = :code_type
                """
            ),
            {"email": email, "code_type": code_type},
        )
        await db_session.execute(
            text(
                """
                INSERT INTO email_code (email, code_type, code, created_at, expires_at)
                VALUES (:email, :code_type, :code, :created_at, :expires_at)
                """
            ),
            {
                "email": email,
                "code_type": code_type,
                "code": code,
                "created_at": now_str(),
                "expires_at": future_str(expire_seconds),
            },
        )

    async def consume(
        self,
        db_session: AsyncSession,
        email: str,
        code_type: str,
        code: str,
    ) -> bool:
        """消费一个未过期且未使用的验证码"""
        result = await db_session.execute(
            text(
                """
                UPDATE email_code
                SET used_at = :now
                WHERE email = :email
                  AND code_type = :code_type
                  AND code = :code
                  AND expires_at > :now
                  AND used_at IS NULL
                """
            ),
            {
                "email": email,
                "code_type": code_type,
                "code": code,
                "now": now_str(),
            },
        )
        return cast(CursorResult[Any], result).rowcount == 1

email_code_repo = EmailCodeRepo()
