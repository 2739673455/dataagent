"""用户模块用例"""

from dataclasses import dataclass

from app.application.shared.schemas import SessionCookieResult
from app.application.shared.session_creator import SessionCreator
from app.core.database import DBSessionContextFactory
from app.core.settings import AuthCfg, EmailCfg
from app.domain.errors import (
    EmailAlreadyExistsError,
    EmailNotFoundError,
    EmailUnchangedError,
    InvalidVerificationCodeError,
    UserDisabledError,
    UsernameUnchangedError,
    UserNotFoundError,
)
from app.domain.ports import (
    EmailCodeRepository,
    EmailSender,
    SessionRepository,
    TokenFactory,
    TokenRepository,
    UserRepository,
)

from .schemas import UserInfoResult


def _session_expire_seconds(cfg: AuthCfg) -> int:
    """会话过期秒数"""
    return cfg.session_expire_days * 24 * 60 * 60


class SendEmailCodeUseCase:
    """发送邮箱验证码（注册/重置邮箱/重置密码）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        auth_config: AuthCfg,
        user_repo: UserRepository,
        email_code_repo: EmailCodeRepository,
        token_factory: TokenFactory,
        email_sender: EmailSender,
    ) -> None:
        self._db = db_factory
        self._cfg = auth_config
        self._users = user_repo
        self._codes = email_code_repo
        self._tf = token_factory
        self._sender = email_sender

    async def execute(self, email: str, code_type: str) -> None:
        """根据 code_type 校验前置条件后发送验证码邮件"""
        async with self._db() as db:
            user = await self._users.get_by_email(db, email)
            if code_type in ("register", "reset_email"):
                if user:
                    raise EmailAlreadyExistsError
            elif code_type == "reset_password":
                if not user:
                    raise EmailNotFoundError
                if not user.yn:
                    raise UserDisabledError

            code = self._tf.verification_code()
            await self._codes.create(
                db, email, code_type, code, self._cfg.email_code_expire_seconds
            )
            await db.commit()

        await self._sender.send_verification_code(email, code, code_type)


class RegisterUserUseCase:
    """用户注册：消费验证码，创建用户并建立会话"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        auth_config: AuthCfg,
        user_repo: UserRepository,
        email_code_repo: EmailCodeRepository,
        session_repo: SessionRepository,
        token_factory: TokenFactory,
    ) -> None:
        self._db = db_factory
        self._cfg = auth_config
        self._users = user_repo
        self._codes = email_code_repo
        self._sessions = session_repo
        self._tf = token_factory

    async def execute(
        self, email: str, code: str, username: str, password: str
    ) -> SessionCookieResult:
        """消费注册验证码，创建用户并返回会话 Cookie 数据"""
        async with self._db() as db:
            if not await self._codes.consume(db, email, "register", code):
                raise InvalidVerificationCodeError
            if await self._users.get_by_email(db, email):
                raise EmailAlreadyExistsError

            user = await self._users.create(db, email, username, password)
            if user.id is None:
                raise RuntimeError("user.id should not be None")

            sid = self._tf.session_id()
            expire = _session_expire_seconds(self._cfg)
            await self._sessions.create(db, sid, user.id, expire)
            await db.commit()

        return SessionCookieResult(session_id=sid, session_expire_seconds=expire)


class UpdateUsernameUseCase:
    """用户修改用户名"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        user_repo: UserRepository,
    ) -> None:
        self._db = db_factory
        self._users = user_repo

    async def execute(self, user_id: int, username: str) -> None:
        """修改用户名；与当前值相同时抛出 UsernameUnchangedError"""
        async with self._db() as db:
            user = await self._users.get_by_id(db, user_id)
            if not user:
                raise UserNotFoundError
            if not user.yn:
                raise UserDisabledError
            if user.name == username:
                raise UsernameUnchangedError

            await self._users.update(db, user, username=username)
            await db.commit()


class UpdateEmailUseCase:
    """用户修改邮箱（需验证码）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        user_repo: UserRepository,
        email_code_repo: EmailCodeRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._users = user_repo
        self._codes = email_code_repo
        self._tokens = token_repo

    async def execute(self, user_id: int, email: str, code: str) -> None:
        """消费验证码后修改邮箱，并清除用户所有令牌"""
        async with self._db() as db:
            if not await self._codes.consume(db, email, "reset_email", code):
                raise InvalidVerificationCodeError

            user = await self._users.get_by_id_with_role_permission(db, user_id)
            if not user:
                raise UserNotFoundError
            if not user.yn:
                raise UserDisabledError
            if user.email == email:
                raise EmailUnchangedError
            if await self._users.get_by_email(db, email):
                raise EmailAlreadyExistsError

            await self._users.update(db, user, email=email)
            await self._tokens.remove_all_by_user(db, user_id)
            await db.commit()


class ResetPasswordUseCase:
    """用户重置密码（需验证码）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        user_repo: UserRepository,
        email_code_repo: EmailCodeRepository,
        session_repo: SessionRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._users = user_repo
        self._codes = email_code_repo
        self._sessions = session_repo
        self._tokens = token_repo

    async def execute(self, email: str, code: str, password: str) -> None:
        """消费验证码后修改密码，并清除用户所有令牌与会话"""
        async with self._db() as db:
            if not await self._codes.consume(db, email, "reset_password", code):
                raise InvalidVerificationCodeError

            user = await self._users.get_by_email(db, email)
            if not user:
                raise UserNotFoundError
            if not user.yn:
                raise UserDisabledError
            if user.id is None:
                raise RuntimeError("user.id should not be None")

            await self._users.update(db, user, password=password)
            await self._tokens.remove_all_by_user(db, user.id)
            await self._sessions.remove_all_by_user(db, user.id)
            await db.commit()


class GetCurrentUserInfoUseCase:
    """获取当前登录用户信息"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        user_repo: UserRepository,
    ) -> None:
        self._db = db_factory
        self._users = user_repo

    async def execute(self, user_id: int) -> UserInfoResult:
        """获取用户基本信息及生效的角色名列表"""
        async with self._db() as db:
            user = await self._users.get_by_id_with_role(db, user_id)
            if not user:
                raise UserNotFoundError
            if not user.yn:
                raise UserDisabledError
            roles = [role.name for role in user.roles if role.yn == 1]

        return UserInfoResult(username=user.name, email=user.email, roles=roles)


@dataclass(frozen=True, slots=True)
class UserUseCases:
    """用户用例集合 — 组合根注入"""

    send_email_code: SendEmailCodeUseCase
    register_user: RegisterUserUseCase
    update_username: UpdateUsernameUseCase
    update_email: UpdateEmailUseCase
    reset_password: ResetPasswordUseCase
    get_current_user_info: GetCurrentUserInfoUseCase


def create_user_use_cases(
    db_factory: DBSessionContextFactory,
    auth_config: AuthCfg,
    email_config: EmailCfg,
    user_repo: UserRepository,
    email_code_repo: EmailCodeRepository,
    session_repo: SessionRepository,
    token_repo: TokenRepository,
    token_factory: TokenFactory,
    email_sender: EmailSender,
    session_creator: SessionCreator,
) -> UserUseCases:
    """工厂函数：创建所有用户用例并注入依赖"""
    return UserUseCases(
        send_email_code=SendEmailCodeUseCase(
            db_factory, auth_config, user_repo, email_code_repo, token_factory, email_sender
        ),
        register_user=RegisterUserUseCase(
            db_factory, auth_config, user_repo, email_code_repo, session_repo, token_factory
        ),
        update_username=UpdateUsernameUseCase(db_factory, user_repo),
        update_email=UpdateEmailUseCase(db_factory, user_repo, email_code_repo, token_repo),
        reset_password=ResetPasswordUseCase(
            db_factory, user_repo, email_code_repo, session_repo, token_repo
        ),
        get_current_user_info=GetCurrentUserInfoUseCase(db_factory, user_repo),
    )
