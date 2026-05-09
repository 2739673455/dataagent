"""签发访问令牌的共享领域服务"""

from app.core.settings import AuthCfg
from app.domain.errors import UserNotFoundError
from app.domain.ports import TokenFactory, TokenRepository, UserRepository


def _active_scopes(user) -> list[str]:
    """从用户生效的角色中提取所有生效的权限名，去重后作为令牌 scope"""
    if not user.roles:
        return []
    return list(
        {
            permission.name
            for role in user.roles
            if role.yn
            for permission in role.permissions
            if permission.yn
        }
    )


class TokenIssuer:
    """签发 access token 的共享服务 — 被 OAuth token 交换流程调用"""

    def __init__(
        self,
        token_repo: TokenRepository,
        user_repo: UserRepository,
        auth_config: AuthCfg,
        token_factory: TokenFactory,
    ) -> None:
        self._token_repo = token_repo
        self._user_repo = user_repo
        self._cfg = auth_config
        self._tf = token_factory

    async def issue(self, db, user_id: int, session_id: str, client_id: str) -> str:
        """查询用户权限范围，生成 access token 并持久化"""
        user = await self._user_repo.get_by_id_with_role_permission(db, user_id)
        if not user:
            raise UserNotFoundError

        access_token = self._tf.access_token()
        expire = self._cfg.access_token_expire_days * 24 * 60 * 60
        await self._token_repo.create(
            db,
            user_id,
            session_id,
            access_token,
            client_id,
            expire,
            _active_scopes(user),
        )
        return access_token
