import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from app.config import CFG
from app.entities.auth import RefreshToken
from app.exceptions.auth import (
    ExpiredAccessTokenError,
    ExpiredRefreshTokenError,
    InsufficientPermissionsError,
    InvalidAccessTokenError,
    InvalidRefreshTokenError,
)
from app.schemas.user import AccessTokenPayload, RefreshTokenPayload
from app.services.database import get_auth_db
from app.utils.context import user_id_ctx
from fastapi import Cookie, Depends, Header
from fastapi.security import SecurityScopes
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

BEIJING_TZ = timezone(timedelta(hours=8))  # 北京时间时区（UTC+8）


def _generate_refresh_token(user_id: int, scopes: list[str]) -> dict:
    """生成刷新令牌"""
    jti = str(uuid.uuid4())  # JWT ID
    expire = datetime.now(BEIJING_TZ) + timedelta(
        days=CFG.auth.refresh_token_expire_days
    )  # 刷新令牌过期时间
    payload = {
        "sub": str(user_id),
        "scope": " ".join(scopes),
        "exp": expire.timestamp(),
        "jti": jti,
    }
    token = jwt.encode(payload, CFG.auth.secret_key, CFG.auth.algorithm)
    return {
        "jti": jti,
        "expire": expire,
        "token": token,
    }


def _generate_access_token(jti: str, user_id: int, scopes: list[str]) -> str:
    """生成访问令牌"""
    expire = datetime.now(BEIJING_TZ) + timedelta(
        minutes=CFG.auth.access_token_expire_minutes
    )  # 访问令牌过期时间
    payload = {
        "sub": str(user_id),
        "scope": " ".join(scopes),
        "exp": expire.timestamp(),
        "jti": jti,
    }
    token = jwt.encode(payload, CFG.auth.secret_key, CFG.auth.algorithm)
    return token


async def create_token(
    db_session: AsyncSession, user_id: int, scopes: list[str]
) -> dict:
    """创建刷新令牌和访问令牌"""
    # 生成刷新令牌
    _rt = _generate_refresh_token(user_id, scopes)
    jti, expire, r_token = _rt["jti"], _rt["expire"], _rt["token"]

    # 存储刷新令牌
    try:
        refresh_token = RefreshToken(jti=jti, user_id=user_id, expires_at=expire)
        db_session.add(refresh_token)
        await db_session.commit()
    except Exception:
        await db_session.rollback()
        raise

    # 生成访问令牌
    a_token = _generate_access_token(jti, user_id, scopes)

    return {
        "access_token": a_token,
        "refresh_token": r_token,
        "token_type": "bearer",
    }


async def revoke_refresh_token(
    db_session: AsyncSession, jti: str, user_id: int
) -> None:
    """在数据库中撤销刷新令牌"""
    try:
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.jti == jti,
                RefreshToken.user_id == user_id,
                RefreshToken.yn == 1,
            )
            .values(yn=0)
        )
        await db_session.execute(stmt)
        await db_session.commit()
    except Exception:
        await db_session.rollback()
        raise


async def revoke_all_refresh_tokens(db_session: AsyncSession, user_id: int) -> None:
    """撤销用户所有刷新令牌"""
    try:
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.yn == 1,
            )
            .values(yn=0)
        )
        await db_session.execute(stmt)
        await db_session.commit()
    except:
        await db_session.rollback()
        raise


# --- 验证访问令牌 ---


def _get_access_token(
    authorization: Annotated[str | None, Header()] = None,  # 从请求头获取 Bearer token
) -> str | None:
    """从请求头获取 Bearer token"""
    if authorization is None:
        raise InvalidAccessTokenError  # 缺少访问令牌
    return authorization.replace("Bearer ", "")


def _decode_access_token(
    access_token: Annotated[str, Depends(_get_access_token)],
) -> AccessTokenPayload:
    """解析访问令牌"""
    try:
        payload = jwt.decode(access_token, CFG.auth.secret_key, [CFG.auth.algorithm])
        payload["scope"] = payload["scope"].split()
        payload = AccessTokenPayload(**payload)
        return payload
    except jwt.ExpiredSignatureError:
        raise ExpiredAccessTokenError  # 访问令牌过期
    except jwt.exceptions.InvalidTokenError or ValidationError:
        raise InvalidAccessTokenError  # 访问令牌无效


async def authenticate_access_token(
    payload: Annotated[AccessTokenPayload, Depends(_decode_access_token)],
    security_scopes: SecurityScopes,
) -> AccessTokenPayload:
    """验证访问令牌"""
    # 设置 user_id 到 ContextVar
    user_id_ctx.set(str(payload.sub))

    # 验证权限范围
    if set(security_scopes.scopes) - set(payload.scope):
        raise InsufficientPermissionsError  # 越权

    return payload


# --- 验证刷新令牌 ---


def _decode_refresh_token(
    refresh_token: Annotated[str, Cookie()],  # 从 Cookie 获取 refresh_token
) -> RefreshTokenPayload:
    """解析刷新令牌"""
    try:
        payload = jwt.decode(refresh_token, CFG.auth.secret_key, [CFG.auth.algorithm])
        payload["scope"] = payload["scope"].split()
        payload = RefreshTokenPayload(**payload)
        return payload
    except jwt.ExpiredSignatureError:
        raise ExpiredRefreshTokenError  # 刷新令牌过期
    except jwt.exceptions.InvalidTokenError or ValidationError:
        raise InvalidRefreshTokenError  # 刷新令牌无效


async def authenticate_refresh_token(
    payload: Annotated[RefreshTokenPayload, Depends(_decode_refresh_token)],
    db_session: Annotated[AsyncSession, Depends(get_auth_db)],
) -> RefreshTokenPayload:
    """验证刷新令牌"""
    # 设置 user_id 到 ContextVar
    user_id_ctx.set(str(payload.sub))

    # 验证刷新令牌是否在数据库中且未被撤销
    stmt = select(RefreshToken.yn, RefreshToken.expires_at).where(
        RefreshToken.jti == payload.jti, RefreshToken.user_id == payload.sub
    )
    result = await db_session.execute(stmt)
    token_record = result.first()

    if not token_record:  # 刷新令牌不存在
        raise InvalidRefreshTokenError

    yn, expires_at = token_record
    if not yn:  # 刷新令牌已被撤销
        raise InvalidRefreshTokenError

    if expires_at.tzinfo is None:  # 确保有时区信息并检查是否过期
        expires_at = expires_at.replace(tzinfo=BEIJING_TZ)
    if datetime.now(BEIJING_TZ) > expires_at:
        raise ExpiredRefreshTokenError  # 刷新令牌过期

    return payload
