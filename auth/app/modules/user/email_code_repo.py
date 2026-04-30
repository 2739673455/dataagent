"""邮箱验证码数据访问"""

from app.utils.datetime_str import future_str, now_str
from sqlalchemy import text
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

    async def get(
        self,
        db_session: AsyncSession,
        email: str,
        code_type: str,
    ) -> str | None:
        """获取该邮箱该类型验证码"""
        result = await db_session.execute(
            text(
                """
                SELECT code
                FROM email_code
                WHERE email = :email
                  AND code_type = :code_type
                  AND expires_at > :now
                """
            ),
            {"email": email, "code_type": code_type, "now": now_str()},
        )
        return result.scalar_one_or_none()


email_code_repo = EmailCodeRepo()
