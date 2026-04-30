"""用户管理服务"""

import random
import secrets
import string
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import aiosmtplib
from app.core.settings import AuthCfg, EmailCfg
from app.core.types import DBSessionContextFactory
from sqlalchemy.ext.asyncio import AsyncSession

from ..shared import errors
from ..shared.schemas import SessionCookieData
from ..shared.session_repo import SessionRepo
from ..shared.token_repo import TokenRepo
from ..shared.user_repo import UserRepo
from .email_code_repo import EmailCodeRepo
from .user_schema import UserResponse


class UserService:
    """用户服务"""

    def __init__(
        self,
        db_session_context_factory: DBSessionContextFactory,
        auth_config: AuthCfg,
        email_config: EmailCfg,
        user_repo: UserRepo,
        email_code_repo: EmailCodeRepo,
        session_repo: SessionRepo,
        token_repo: TokenRepo,
    ) -> None:
        self.db_session_context_factory = db_session_context_factory
        self.auth_config = auth_config
        self.email_config = email_config
        self.user_repo = user_repo
        self.email_code_repo = email_code_repo
        self.session_repo = session_repo
        self.token_repo = token_repo

    @staticmethod
    def _require_user_id(user_id: int | None) -> int:
        """确保用户 ID 已存在"""
        if user_id is None:
            raise RuntimeError("user.id should not be None")
        return user_id

    async def _create_email_code(
        self,
        db_session: AsyncSession,
        email: str,
        code_type: str,
        expire_seconds: int,
    ) -> str:
        """创建邮箱验证码"""
        code = "".join(random.choices(string.digits, k=6))
        await self.email_code_repo.create(
            db_session,
            email,
            code_type,
            code,
            expire_seconds,
        )
        return code

    async def _send_email_message(
        self,
        to_email: str,
        code: str,
        code_type: str,
    ) -> None:
        """发送验证码邮件"""
        type_text = {
            "register": "注册",
            "reset_email": "重置邮箱",
            "reset_password": "重置密码",
        }[code_type]
        subject = f"您的{type_text}验证码"
        html_content = f"""
        <html>
        <body style=\"font-family: Arial, sans-serif; padding: 20px;\">
            <h2 style=\"color: #333;\">{type_text}验证码</h2>
            <p>您的{type_text}验证码是：</p>
            <p style=\"font-size: 24px; font-weight: bold; color: #007bff; letter-spacing: 4px;\">
                {code}
            </p>
            <p style=\"color: #666; font-size: 14px;\">
                验证码有效期为 10 分钟，请尽快使用。
            </p>
            <p style=\"color: #999; font-size: 12px;\">
                如果您没有进行此操作，请忽略此邮件。
            </p>
        </body>
        </html>
        """

        msg = MIMEMultipart()
        msg["From"] = (
            formataddr(
                (
                    Header(self.email_config.from_name, "utf-8").encode(),
                    self.email_config.from_email,
                )
            )
            if self.email_config.from_name
            else self.email_config.from_email
        )
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=self.email_config.smtp_host,
            port=self.email_config.smtp_port,
            username=self.email_config.smtp_user,
            password=self.email_config.smtp_password,
            use_tls=True,
        )

    async def _consume_email_code(
        self,
        db_session: AsyncSession,
        email: str,
        code_type: str,
        code: str,
    ) -> bool:
        """验证并消费邮箱验证码"""
        return await self.email_code_repo.consume(
            db_session,
            email,
            code_type,
            code,
        )

    async def send_email_code(self, email: str, code_type: str) -> None:
        """发送邮箱验证码"""
        async with self.db_session_context_factory() as db_session:
            # 获取用户
            user = await self.user_repo.get_by_email(db_session, email)
            # 注册或重置邮箱，检查邮箱是否已存在
            if code_type in ["register", "reset_email"]:
                if user:
                    raise errors.EmailAlreadyExistsError
            # 重置密码，检查用户是否存在或被禁用
            elif code_type == "reset_password":
                if not user:
                    raise errors.EmailNotFoundError
                if not user.yn:
                    raise errors.UserDisabledError

            # 创建验证码
            code = await self._create_email_code(
                db_session,
                email,
                code_type,
                self.auth_config.email_code_expire_seconds,
            )
            await db_session.commit()

        # 发送验证码邮件
        await self._send_email_message(email, code, code_type)

    async def register(
        self,
        email: str,
        code: str,
        username: str,
        password: str,
    ) -> SessionCookieData:
        """注册新用户"""
        async with self.db_session_context_factory() as db_session:
            # 验证验证码
            if not await self._consume_email_code(db_session, email, "register", code):
                raise errors.InvalidVerificationCodeError
            # 检查邮箱是否已存在
            if await self.user_repo.get_by_email(db_session, email):
                raise errors.EmailAlreadyExistsError

            # 创建用户
            user = await self.user_repo.create(
                db_session,
                email,
                username,
                password,
            )
            user_id = self._require_user_id(user.id)

            # 创建会话
            session_id = secrets.token_urlsafe(32)
            session_expire_seconds = self.auth_config.session_expire_days * 24 * 60 * 60
            await self.session_repo.create_session(
                db_session,
                session_id,
                user_id,
                session_expire_seconds,
            )
            await db_session.commit()

        return SessionCookieData(
            session_id=session_id,
            session_expire_seconds=session_expire_seconds,
        )

    async def update_username(
        self,
        user_id: int,
        username: str,
    ) -> None:
        """修改用户名"""
        async with self.db_session_context_factory() as db_session:
            # 获取用户
            user = await self.user_repo.get_by_id(db_session, user_id)
            # 检查用户是否存在，是否被禁用，用户名是否相同
            if not user:
                raise errors.UserNotFoundError
            if not user.yn:
                raise errors.UserDisabledError
            if user.name == username:
                raise errors.UsernameUnchangedError

            # 更新用户名
            user = await self.user_repo.update(db_session, user, username=username)
            await db_session.commit()

    async def update_email(
        self,
        user_id: int,
        email: str,
        code: str,
    ) -> None:
        """修改邮箱"""
        async with self.db_session_context_factory() as db_session:
            # 验证验证码
            if not await self._consume_email_code(
                db_session, email, "reset_email", code
            ):
                raise errors.InvalidVerificationCodeError
            # 获取用户
            user = await self.user_repo.get_by_id_with_role_permission(
                db_session, user_id
            )
            # 检查用户是否存在，是否被禁用，邮箱是否相同，邮箱是否已存在
            if not user:
                raise errors.UserNotFoundError
            if not user.yn:
                raise errors.UserDisabledError
            if user.email == email:
                raise errors.EmailUnchangedError
            if await self.user_repo.get_by_email(db_session, email):
                raise errors.EmailAlreadyExistsError

            # 更新邮箱
            user = await self.user_repo.update(db_session, user, email=email)
            # 撤销用户所有访问令牌
            await self.token_repo.remove_all_tokens_by_user(db_session, user_id)
            await db_session.commit()

    async def update_password(
        self,
        email: str,
        code: str,
        password: str,
    ) -> None:
        """通过邮箱验证码重置密码"""
        async with self.db_session_context_factory() as db_session:
            # 验证验证码
            if not await self._consume_email_code(
                db_session,
                email,
                "reset_password",
                code,
            ):
                raise errors.InvalidVerificationCodeError
            # 获取用户
            user = await self.user_repo.get_by_email(db_session, email)
            # 检查用户是否存在，是否被禁用
            if not user:
                raise errors.UserNotFoundError
            if not user.yn:
                raise errors.UserDisabledError
            user_id = self._require_user_id(user.id)

            # 更新密码
            user = await self.user_repo.update(db_session, user, password=password)
            # 撤销用户所有会话和访问令牌
            await self.token_repo.remove_all_tokens_by_user(db_session, user_id)
            await self.session_repo.remove_all_sessions(db_session, user_id)
            await db_session.commit()

    async def get_userinfo(self, user_id: int) -> UserResponse:
        """获取当前用户信息"""
        async with self.db_session_context_factory() as db_session:
            # 获取用户
            user = await self.user_repo.get_by_id_with_role(db_session, user_id)
            # 检查用户是否存在，是否被禁用
            if not user:
                raise errors.UserNotFoundError
            if not user.yn:
                raise errors.UserDisabledError

            # 获取用户角色
            roles = [role.name for role in user.roles if role.yn == 1]

        return UserResponse(
            username=user.name,
            email=user.email,
            roles=roles,
        )
