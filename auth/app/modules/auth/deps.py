import json
from typing import Annotated

import jwt
from fastapi import Header
from loguru import logger
from sqlalchemy import text

from app.core import cfg, context, get_db_session_context
from app.utils.datetime_str import now_str

from .auth_schema import AccessTokenPayload


async def resolve_access_token_from_header(
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenPayload | None:
    """从 header 获取 token，解析，查库"""
    access_token = None

    # 从请求头获取访问令牌
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer" and credentials:
            access_token = credentials
    if not access_token:
        return None

    # 解析访问令牌
    try:
        payload_data = jwt.decode(
            access_token,
            cfg.auth.secret_key,
            [cfg.auth.algorithm],
        )
        payload = AccessTokenPayload(**payload_data)
    except Exception as e:
        logger.exception(e)
        return None

    # 查库获取 token 信息
    async with get_db_session_context(cfg.db.selected, cfg.db.driver) as db_session:
        result = await db_session.execute(
            text(
                """
                SELECT user_id, scopes_json
                FROM access_tokens
                WHERE jti = :jti
                  AND expires_at > :now
                LIMIT 1
                """
            ),
            {"jti": payload.jti, "now": now_str()},
        )
        token_record = result.mappings().first()
    if token_record is None or token_record["user_id"] != int(payload.sub):
        return None

    # 设置 scope
    payload.scope = json.loads(token_record["scopes_json"])

    context.user_id_ctx.set(str(payload.sub))

    return payload
