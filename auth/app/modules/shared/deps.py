import json
from datetime import datetime
from typing import Annotated

from fastapi import Header

from app.core import cfg, context, get_db_session_context
from app.utils.datetime_str import now_str

from .errors import InvalidAccessTokenError
from .schemas import AccessTokenPayload


async def authenticate_access_token(
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenPayload:
    payload = await resolve_access_token_from_header(authorization)
    if payload is None:
        raise InvalidAccessTokenError
    return payload


async def resolve_access_token_from_header(
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenPayload | None:
    """从 Authorization header 获取 opaque access token 并查库校验。"""
    access_token = None

    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer" and credentials:
            access_token = credentials
    if not access_token:
        return None

    async with get_db_session_context(cfg.db.selected, cfg.db.driver) as db_session:
        from sqlalchemy import text

        result = await db_session.execute(
            text(
                """
                SELECT access_token, user_id, scope, expires_at
                FROM access_token
                WHERE access_token = :access_token
                  AND expires_at > :now
                  AND revoked_at IS NULL
                LIMIT 1
                """
            ),
            {"access_token": access_token, "now": now_str()},
        )
        token_record = result.mappings().first()

    if token_record is None:
        return None

    context.user_id_ctx.set(str(token_record["user_id"]))

    expires_at = datetime.fromisoformat(str(token_record["expires_at"])).timestamp()
    scope = json.loads(token_record["scope"] or "[]")
    return AccessTokenPayload(
        access_token=token_record["access_token"],
        sub=token_record["user_id"],
        exp=expires_at,
        scope=scope,
    )
