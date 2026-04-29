"""认证模块服务"""

import secrets
import uuid
from datetime import datetime, timedelta

import jwt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cfg
from app.core.settings import AuthCfg
from app.core.types import DBSessionContextFactory

from ..user.user_error import UserNotFoundError
from ..user.user_repo import UserRepo
from . import auth_error
from .auth_code_repo import AuthCodeRepo
from .session_repo import SessionRepo
from .token_repo import TokenRepo


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
        user_repo: UserRepo,
        auth_code_repo: AuthCodeRepo,
        session_repo: SessionRepo,
        token_repo: TokenRepo,
    ) -> None:
        self.db_session_context_factory = db_session_context_factory
        self.auth_config = auth_config
        self.user_repo = user_repo
        self.auth_code_repo = auth_code_repo
        self.session_repo = session_repo
        self.token_repo = token_repo

    async def _create_access_token(
        self,
        db_session: AsyncSession,
        user_id: int,
        session_id: str,
    ) -> str:
        """创建并存储访问令牌"""
        # 生成访问令牌
        jti = str(uuid.uuid4())
        token = jwt.encode(
            {
                "jti": jti,
                "sub": str(user_id),
                "exp": (
                    datetime.now()
                    + timedelta(days=self.auth_config.access_token_expire_days)
                ).timestamp(),
            },
            self.auth_config.secret_key,
            self.auth_config.algorithm,
        )

        # 获取用户
        user = await self.user_repo.get_by_id_with_role_permission(db_session, user_id)
        if not user:
            raise UserNotFoundError
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
            jti,
            expire_seconds,
            scopes,
        )

        return token

    async def create_authorization_code(
        self,
        session_id: str | None,
        client_id: str,
        redirect_uri: str,
    ) -> AuthorizeResult | None:
        """创建授权码"""
        if not session_id:
            return None

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
            if session_data.session_id is None:
                raise RuntimeError("session_data.session_id should not be None")

            # 生成授权码
            code = secrets.token_urlsafe(32)
            await self.auth_code_repo.create_auth_code(
                db_session,
                code=code,
                user_id=session_data.user_id,
                session_id=session_data.session_id,
                client_id=client_id,
                redirect_uri=redirect_uri,
                expire_seconds=cfg.auth.auth_code_expire_seconds,
            )
            await db_session.commit()

        return AuthorizeResult(
            code=code,
            session_id=session_data.session_id,
            session_expire_seconds=session_expire_seconds,
        )

    async def exchange_token(
        self, code: str, client_id: str, client_secret: str | None
    ) -> str:
        """使用授权码换取访问令牌"""
        async with self.db_session_context_factory() as db_session:
            # 获取授权码信息
            auth_code_data = await self.auth_code_repo.consume_auth_code(
                db_session, code
            )
            if auth_code_data is None:
                raise auth_error.InvalidGrantError(detail="授权码不存在或已过期")
            if auth_code_data.client_id != client_id:
                raise auth_error.InvalidGrantError(detail="授权码校验失败")

            # 创建访问令牌
            access_token = await self._create_access_token(
                db_session,
                auth_code_data.user_id,
                auth_code_data.session_id,
            )
            await db_session.commit()

        return access_token
