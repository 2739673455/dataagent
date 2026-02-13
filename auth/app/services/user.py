"""用户管理"""

from typing import Literal

from app.entities.auth import Group, Scope, User
from app.exceptions.user import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    UserDisabledError,
    UserEmailSameError,
    UserNameSameError,
    UserNotFoundError,
    UserPasswordSameError,
)
from app.services.auth import create_token
from fastapi import Response
from pwdlib._hash import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

passwd_hash = PasswordHash.recommended()
HASHED_DUMMY_PASSWORD = passwd_hash.hash("dummy_password")


async def verify_email_exists(db_session: AsyncSession, email: str) -> None:
    """验证邮箱是否已存在"""
    stmt = select(User).where(User.email == email)
    result = await db_session.execute(stmt)
    if result.scalar_one_or_none():
        raise EmailAlreadyExistsError


def verify_password(user: User, password: str) -> None:
    """验证密码"""
    # 使用 dummy_password 避免时序攻击
    target_hash = user.password_hash if user else HASHED_DUMMY_PASSWORD
    password_correct = passwd_hash.verify(password, target_hash)
    if not password_correct:
        raise InvalidCredentialsError  # 邮箱或密码错误


async def get_default_group(db_session: AsyncSession) -> list[Group]:
    """获取默认组"""
    stmt = select(Group).where(Group.id == 1)
    result = await db_session.execute(stmt)
    group = result.scalar_one_or_none()
    return [group] if group else []


async def get_user(
    db_session: AsyncSession,
    user_id: int | None = None,
    email: str | None = None,
    options: Literal["group", "scope"] | None = None,
) -> tuple[User, list[str], list[str]]:
    """通过 user_id 或 email 获取用户信息，可附带组信息和权限范围信息"""
    if user_id:
        stmt = select(User).where(User.id == user_id)
    elif email:
        stmt = select(User).where(User.email == email)
    else:
        raise ValueError("user_id or email must be provided")

    if options == "group":
        stmt = stmt.options(selectinload(User.group.and_(Group.yn == 1)))
    elif options == "scope":
        stmt = stmt.options(
            selectinload(User.group.and_(Group.yn == 1)).selectinload(
                Group.scope.and_(Scope.yn == 1)
            )
        )

    result = await db_session.execute(stmt)
    user = result.unique().scalar_one_or_none()
    if not user:
        raise UserNotFoundError  # 用户不存在
    elif not user.yn:
        raise UserDisabledError  # 用户已被禁用

    groups = [g.name for g in user.group] if options in ["group", "scope"] else []
    scopes = (
        list(set([s.name for g in user.group for s in g.scope]))
        if options == "scope"
        else []
    )

    return user, groups, scopes


async def add_user_in_db(
    db_session: AsyncSession,
    email: str,
    username: str,
    password: str,
    groups: list[Group],
) -> User:
    """将用户加入数据库"""
    try:
        # 创建用户
        user = User(
            email=email,
            name=username,
            password_hash=passwd_hash.hash(password),
            group=groups,
        )
        # 添加用户
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user
    except IntegrityError as e:
        await db_session.rollback()
        # 检查是否是邮箱唯一约束冲突
        if "user.email" in str(e) or "Duplicate entry" in str(e):
            raise EmailAlreadyExistsError from e
        raise
    except Exception:
        await db_session.rollback()
        raise


async def update_username(
    db_session: AsyncSession, user_id: int, user_name: str
) -> None:
    """修改用户名"""
    try:
        # 获取用户信息
        user, _, _ = await get_user(db_session, user_id)
        if user.name == user_name:
            raise UserNameSameError  # 用户名与原用户名相同
        # 更新用户名
        user.name = user_name
        await db_session.commit()
    except Exception:
        await db_session.rollback()
        raise


async def update_email(db_session: AsyncSession, user_id: int, email: str) -> None:
    """修改邮箱"""
    try:
        # 获取用户信息
        user, _, _ = await get_user(db_session, user_id)
        if user.email == email:
            raise UserEmailSameError  # 邮箱与原邮箱相同
        # 检查邮箱是否已被使用
        stmt = select(User).where(User.email == email, User.id != user.id)
        result = await db_session.execute(stmt)
        if result.scalar_one_or_none():
            raise EmailAlreadyExistsError  # 邮箱已被使用
        # 更新邮箱
        user.email = email
        await db_session.commit()
    except Exception:
        await db_session.rollback()
        raise


async def update_password(
    db_session: AsyncSession, user_id: int, password: str
) -> None:
    """修改密码"""
    try:
        user, _, _ = await get_user(db_session, user_id)
        if passwd_hash.verify(password, user.password_hash):
            raise UserPasswordSameError  # 密码与原密码相同
        # 更新密码
        user.password_hash = passwd_hash.hash(password)
        await db_session.commit()
    except Exception:
        await db_session.rollback()
        raise


async def login_by_user_id(db_session: AsyncSession, user_id: int, response: Response):
    """通过 user_id 登录"""
    # 获取用户信息，包含权限信息
    user, _, scopes = await get_user(db_session, user_id, options="scope")
    # 创建访问令牌和刷新令牌
    tokens = await create_token(db_session, user.id, scopes)
    # 在 Cookie 中设置 refresh_token
    response.set_cookie(
        key="refresh_token",  # Cookie 名称，用于存储刷新令牌
        value=tokens["refresh_token"],  # 从 JWT 创建的刷新令牌值
        httponly=True,  # 防止 JavaScript 访问 Cookie
        secure=False,  # 在 HTTP 和 HTTPS 下都可以发送
        samesite="lax",  # 防止 CSRF 攻击，允许跨站 GET 请求时发送 Cookie
    )
    return user, tokens
