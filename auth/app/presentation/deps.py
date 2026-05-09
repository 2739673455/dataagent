"""FastAPI 依赖 — 令牌解析与认证"""

import json
from datetime import datetime
from typing import Annotated

from fastapi import Header

from app.core import cfg, context, get_db_session_context
from app.domain.errors import InvalidAccessTokenError
from app.domain.ports import TokenRepository


class AccessTokenPayload:
    """由 deps 解析后的令牌载荷 — 不是领域对象，仅用于 FastAPI 注入"""

    def __init__(self, access_token: str, sub: int, exp: float, scope: list[str]) -> None:
        self.access_token = access_token
        self.sub = sub
        self.exp = exp
        self.scope = scope


async def resolve_access_token_from_header(
    authorization: Annotated[str | None, Header()] = None,
    token_repo: TokenRepository | None = None,
) -> AccessTokenPayload | None:
    """从 Authorization header 解析 opaque access token 并查库校验"""
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        return None

    async with get_db_session_context(cfg.db.selected, cfg.db.driver) as db:
        token_record = await token_repo.get_active(db, credentials) if token_repo else None

    if token_record is None:
        return None

    context.user_id_ctx.set(str(token_record.user_id))
    expires_at = datetime.fromisoformat(token_record.expires_at).timestamp()
    scope = json.loads(token_record.scope or "[]")
    return AccessTokenPayload(
        access_token=token_record.access_token,
        sub=token_record.user_id,
        exp=expires_at,
        scope=scope,
    )


def authenticate_access_token(
    token_repo: TokenRepository,
):
    """返回 FastAPI Depends — 校验访问令牌"""

    async def _authenticate(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AccessTokenPayload:
        payload = await resolve_access_token_from_header(authorization, token_repo)
        if payload is None:
            raise InvalidAccessTokenError
        return payload

    return _authenticate
