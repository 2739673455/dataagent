"""认证模块服务"""

import base64
import hashlib
import re
import secrets

from app.core import cfg
from app.core.settings import AuthCfg
from app.core.types import DBSessionContextFactory
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..shared import errors
from ..shared.schemas import SessionCookieData
from ..shared.session_repo import SessionRepo
from ..shared.token_repo import TokenRepo
from ..shared.user_repo import UserRepo, passwd_hash
from .auth_code_repo import AuthCodeRepo


class AuthorizeResult(BaseModel):
    code: str
    session_id: str
    session_expire_seconds: int


class AuthService:
    """认证服务"""

    def __init__(
        self,
        db_session_context_factory: DBSessionContextFactory,
        auth_config: AuthCfg,
        auth_code_repo: AuthCodeRepo,
        session_repo: SessionRepo,
        token_repo: TokenRepo,
        user_repo: UserRepo,
    ) -> None:
        self.db_session_context_factory = db_session_context_factory
        self.auth_config = auth_config
        self.auth_code_repo = auth_code_repo
        self.session_repo = session_repo
        self.token_repo = token_repo
        self.user_repo = user_repo

    @staticmethod
    def _validate_base64url_43(value: str) -> bool:
        return re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None

    @staticmethod
    def _create_code_challenge(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    async def _create_access_token(
        self,
        db_session: AsyncSession,
        user_id: int,
        session_id: str,
        client_id: str,
    ) -> str:
        """创建并存储访问令牌"""
        token = secrets.token_urlsafe(32)

        # 获取用户
        user = await self.user_repo.get_by_id_with_role_permission(db_session, user_id)
        if not user:
            raise errors.UserNotFoundError
        # 获取权限
        scopes = []
        if user.roles:
            scopes = list(
                {
                    permission.name
                    for role in user.roles
                    if role.yn
                    for permission in role.permissions
                    if permission.yn
                }
            )

        # 保存访问令牌
        expire_seconds = self.auth_config.access_token_expire_days * 24 * 60 * 60
        await self.token_repo.create_token(
            db_session,
            user_id,
            session_id,
            token,
            client_id,
            expire_seconds,
            scopes,
        )

        return token

    async def create_authorization_code(
        self,
        session_id: str | None,
        response_type: str | None,
        client_id: str | None,
        redirect_uri: str | None,
        state: str | None,
        code_challenge: str | None,
        code_challenge_method: str | None,
    ) -> AuthorizeResult | None:
        """创建授权码"""
        if not session_id:
            return None
        if response_type != "code":
            raise errors.InvalidAuthorizationRequestError(
                detail="response_type 必须为 code"
            )
        if not client_id:
            raise errors.InvalidAuthorizationRequestError(detail="client_id 缺失")
        if not redirect_uri:
            raise errors.InvalidAuthorizationRequestError(detail="redirect_uri 缺失")
        if not state or not self._validate_base64url_43(state):
            raise errors.InvalidAuthorizationRequestError(detail="state 不合法")
        if not code_challenge or not self._validate_base64url_43(code_challenge):
            raise errors.InvalidAuthorizationRequestError(
                detail="code_challenge 不合法"
            )
        if code_challenge_method != "S256":
            raise errors.InvalidAuthorizationRequestError(
                detail="code_challenge_method 必须为 S256"
            )

        session_expire_seconds = cfg.auth.session_expire_days * 24 * 60 * 60
        async with self.db_session_context_factory() as db_session:
            # 获取会话
            session_data = await self.session_repo.get_and_refresh_session(
                db_session,
                session_id,
                session_expire_seconds,
            )
            if not session_data:
                return None

            # 生成授权码
            code = secrets.token_urlsafe(32)
            await self.auth_code_repo.create_auth_code(
                db_session,
                code=code,
                user_id=session_data.user_id,
                session_id=session_data.session_id,
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                expire_seconds=cfg.auth.auth_code_expire_seconds,
            )
            await db_session.commit()

        return AuthorizeResult(
            code=code,
            session_id=session_data.session_id,
            session_expire_seconds=session_expire_seconds,
        )

    async def exchange_token(
        self,
        grant_type: str,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> str:
        """使用授权码换取访问令牌"""
        if grant_type != "authorization_code":
            raise errors.InvalidGrantError(detail="grant_type 不合法")
        if not self._validate_base64url_43(code_verifier):
            raise errors.InvalidGrantError(detail="code_verifier 不合法")

        async with self.db_session_context_factory() as db_session:
            # 获取授权码信息
            auth_code_data = await self.auth_code_repo.get_active_auth_code(
                db_session, code
            )
            if auth_code_data is None:
                raise errors.InvalidGrantError(detail="授权码不存在或已过期")
            if auth_code_data.client_id != client_id:
                raise errors.InvalidGrantError(detail="授权码校验失败")
            if auth_code_data.redirect_uri != redirect_uri:
                raise errors.InvalidGrantError(detail="授权码校验失败")
            if auth_code_data.code_challenge_method != "S256":
                raise errors.InvalidGrantError(detail="授权码校验失败")
            expected_code_challenge = self._create_code_challenge(code_verifier)
            if expected_code_challenge != auth_code_data.code_challenge:
                raise errors.InvalidGrantError(detail="PKCE 校验失败")
            await self.auth_code_repo.mark_used(db_session, code)

            # 创建访问令牌
            access_token = await self._create_access_token(
                db_session,
                auth_code_data.user_id,
                auth_code_data.session_id,
                auth_code_data.client_id,
            )
            await db_session.commit()

        return access_token

    async def create_session(self, user_id: int) -> SessionCookieData:
        """为用户创建登录会话"""
        session_id = secrets.token_urlsafe(32)
        session_expire_seconds = self.auth_config.session_expire_days * 24 * 60 * 60
        async with self.db_session_context_factory() as db_session:
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

    async def login(self, email: str, password: str) -> SessionCookieData:
        """用户登录"""
        async with self.db_session_context_factory() as db_session:
            user = await self.user_repo.get_by_email_with_role_permission(
                db_session,
                email,
            )
            if not user:
                raise errors.UserNotFoundError
            if not user.yn:
                raise errors.UserDisabledError
            if not passwd_hash.verify(password, user.password_hash):
                raise errors.InvalidCredentialsError
            user_id = user.id
            if user_id is None:
                raise RuntimeError("user.id should not be None")

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

    async def logout(self, access_token: str, session_id: str | None) -> None:
        """登出当前用户"""
        async with self.db_session_context_factory() as db_session:
            await self.token_repo.remove_token(db_session, access_token)
            if session_id:
                await self.token_repo.remove_all_tokens_by_session(
                    db_session, session_id
                )
                await self.session_repo.remove_session(db_session, session_id)
            await db_session.commit()
