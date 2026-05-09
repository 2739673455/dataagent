"""OAuth 模块用例"""

from dataclasses import dataclass

from app.application.shared.schemas import SessionCookieResult
from app.application.shared.session_creator import SessionCreator
from app.application.shared.token_issuer import TokenIssuer
from app.core.database import DBSessionContextFactory
from app.core.settings import AuthCfg
from app.domain.errors import (
    InvalidAuthorizationRequestError,
    InvalidCredentialsError,
    InvalidGrantError,
    UserDisabledError,
)
from app.domain.ports import (
    AuthCodeRepository,
    PasswordHasher,
    PkceService,
    SessionRepository,
    TokenFactory,
    TokenRepository,
    UserRepository,
)

from .schemas import AuthorizeResult, TokenResult


def _session_expire_seconds(cfg: AuthCfg) -> int:
    """会话过期秒数"""
    return cfg.session_expire_days * 24 * 60 * 60


class AuthorizeUseCase:
    """OAuth 授权端点：校验请求参数，生成授权码"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        auth_config: AuthCfg,
        auth_code_repo: AuthCodeRepository,
        session_repo: SessionRepository,
        token_factory: TokenFactory,
        pkce: PkceService,
    ) -> None:
        self._db = db_factory
        self._cfg = auth_config
        self._codes = auth_code_repo
        self._sessions = session_repo
        self._tf = token_factory
        self._pkce = pkce

    async def execute(
        self,
        session_id: str | None,
        response_type: str | None,
        client_id: str | None,
        redirect_uri: str | None,
        state: str | None,
        code_challenge: str | None,
        code_challenge_method: str | None,
    ) -> AuthorizeResult | None:
        """校验授权请求并生成授权码；未登录返回 None，参数不合法抛出异常"""
        if not session_id:
            return None
        if response_type != "code":
            raise InvalidAuthorizationRequestError(detail="response_type 必须为 code")
        if not client_id:
            raise InvalidAuthorizationRequestError(detail="client_id 缺失")
        if not redirect_uri:
            raise InvalidAuthorizationRequestError(detail="redirect_uri 缺失")
        if not state or not self._pkce.validate_base64url_43(state):
            raise InvalidAuthorizationRequestError(detail="state 不合法")
        if not code_challenge or not self._pkce.validate_base64url_43(code_challenge):
            raise InvalidAuthorizationRequestError(detail="code_challenge 不合法")
        if code_challenge_method != "S256":
            raise InvalidAuthorizationRequestError(detail="code_challenge_method 必须为 S256")

        expire = _session_expire_seconds(self._cfg)
        async with self._db() as db:
            session = await self._sessions.get_and_refresh(db, session_id, expire)
            if not session:
                return None

            code = self._tf.authorization_code()
            await self._codes.create(
                db,
                code=code,
                user_id=session.user_id,
                session_id=session.session_id,
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                expire_seconds=self._cfg.auth_code_expire_seconds,
            )
            await db.commit()

        return AuthorizeResult(
            code=code,
            redirect_uri=redirect_uri,
            session_id=session.session_id,
            session_expire_seconds=expire,
            state=state,
        )


class ExchangeTokenUseCase:
    """OAuth 令牌端点：用授权码 + PKCE 换取 access token"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        auth_code_repo: AuthCodeRepository,
        token_issuer: TokenIssuer,
        pkce: PkceService,
    ) -> None:
        self._db = db_factory
        self._codes = auth_code_repo
        self._issuer = token_issuer
        self._pkce = pkce

    async def execute(
        self,
        grant_type: str,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> TokenResult:
        """校验授权码与 PKCE，签发 access token"""
        if grant_type != "authorization_code":
            raise InvalidGrantError(detail="grant_type 不合法")
        if not self._pkce.validate_base64url_43(code_verifier):
            raise InvalidGrantError(detail="code_verifier 不合法")

        async with self._db() as db:
            auth_code = await self._codes.get_active(db, code)
            if auth_code is None:
                raise InvalidGrantError(detail="授权码不存在或已过期")
            if auth_code.client_id != client_id:
                raise InvalidGrantError(detail="授权码校验失败")
            if auth_code.redirect_uri != redirect_uri:
                raise InvalidGrantError(detail="授权码校验失败")
            if auth_code.code_challenge_method != "S256":
                raise InvalidGrantError(detail="授权码校验失败")

            expected = self._pkce.create_code_challenge(code_verifier)
            if expected != auth_code.code_challenge:
                raise InvalidGrantError(detail="PKCE 校验失败")

            await self._codes.mark_used(db, code)
            access_token = await self._issuer.issue(
                db, auth_code.user_id, auth_code.session_id, auth_code.client_id
            )
            await db.commit()

        return TokenResult(access_token=access_token)


class LoginUseCase:
    """OAuth 登录：校验邮箱密码，创建会话"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        user_repo: UserRepository,
        password_hasher: PasswordHasher,
        session_creator: SessionCreator,
    ) -> None:
        self._db = db_factory
        self._users = user_repo
        self._hasher = password_hasher
        self._sessions = session_creator

    async def execute(self, email: str, password: str) -> SessionCookieResult:
        """校验凭证并创建会话；凭证错误或用户禁用时抛出异常"""
        async with self._db() as db:
            user = await self._users.get_by_email(db, email)
            if not user:
                raise InvalidCredentialsError
            if not user.yn:
                raise UserDisabledError
            if not self._hasher.verify(password, user.password_hash):
                raise InvalidCredentialsError
            if user.id is None:
                raise RuntimeError("user.id should not be None")

            result = await self._sessions.create(db, user.id)
            await db.commit()
        return result


class LogoutUseCase:
    """OAuth 登出：清除访问令牌和会话"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        session_repo: SessionRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._sessions = session_repo
        self._tokens = token_repo

    async def execute(self, access_token: str, session_id: str | None) -> None:
        """清除当前访问令牌，若有关联会话则一并清除"""
        async with self._db() as db:
            await self._tokens.remove(db, access_token)
            if session_id:
                await self._tokens.remove_all_by_session(db, session_id)
                await self._sessions.remove(db, session_id)
            await db.commit()


@dataclass(frozen=True, slots=True)
class OAuthUseCases:
    """OAuth 用例集合 — 组合根注入"""

    authorize: AuthorizeUseCase
    exchange_token: ExchangeTokenUseCase
    login: LoginUseCase
    logout: LogoutUseCase


def create_oauth_use_cases(
    db_factory: DBSessionContextFactory,
    auth_config: AuthCfg,
    auth_code_repo: AuthCodeRepository,
    session_repo: SessionRepository,
    token_repo: TokenRepository,
    user_repo: UserRepository,
    password_hasher: PasswordHasher,
    token_factory: TokenFactory,
    pkce: PkceService,
    token_issuer: TokenIssuer,
    session_creator: SessionCreator,
) -> OAuthUseCases:
    """工厂函数：创建所有 OAuth 用例并注入依赖"""
    return OAuthUseCases(
        authorize=AuthorizeUseCase(
            db_factory, auth_config, auth_code_repo, session_repo, token_factory, pkce
        ),
        exchange_token=ExchangeTokenUseCase(db_factory, auth_code_repo, token_issuer, pkce),
        login=LoginUseCase(db_factory, user_repo, password_hasher, session_creator),
        logout=LogoutUseCase(db_factory, session_repo, token_repo),
    )
