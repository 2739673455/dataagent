from typing import Annotated

from app.schemas.auth import (
    AccessTokenPayload,
    LoginRequest,
    LoginResponse,
    RefreshTokenPayload,
    RegisterRequest,
    UpdateEmailRequest,
    UpdatePasswordRequest,
    UpdateUsernameRequest,
    UserResponse,
)
from app.services.auth import (
    authenticate_access_token,
    authenticate_refresh_token,
    create_token,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
)
from app.services.user import (
    add_user_in_db,
    get_default_group,
    get_user,
    login_by_user_id,
    update_email,
    update_password,
    update_username,
    verify_email_exists,
    verify_password,
)
from app.utils.context import user_id_ctx
from app.utils.database import get_auth_db
from app.utils.log import logger
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/register", response_model=LoginResponse)
async def api_register(
    body: RegisterRequest,
    db_session: Annotated[AsyncSession, Depends(get_auth_db)],
    response: Response,
) -> LoginResponse:
    """注册新用户"""
    # 验证邮箱是否已存在
    await verify_email_exists(db_session, body.email)
    # 获取默认组
    groups = await get_default_group(db_session)
    # 将用户加入数据库，同时关联默认组
    user = await add_user_in_db(
        db_session, body.email, body.username, body.password, groups
    )
    # 登录
    user, tokens = await login_by_user_id(db_session, user.id, response)
    # 设置 user_id 到 ContextVar
    user_id_ctx.set(str(user.id))
    logger.info("User register")
    return LoginResponse(**tokens)


@router.post("/login", response_model=LoginResponse)
async def api_login(
    body: LoginRequest,
    db_session: Annotated[AsyncSession, Depends(get_auth_db)],
    response: Response,
) -> LoginResponse:
    """用户登录"""
    # 通过邮箱获取用户信息，包含权限信息
    user, _, scopes = await get_user(db_session, email=body.email, options="scope")
    # 验证密码
    verify_password(user, body.password)
    # 创建访问令牌和刷新令牌
    tokens = await create_token(db_session, user.id, scopes)
    # 在 Cookie 中设置 refresh_token
    response.set_cookie(
        key="refresh_token",
        value=tokens["refresh_token"],
        httponly=True,
        secure=False,
        samesite="lax",
    )
    # 设置 user_id 到 ContextVar
    user_id_ctx.set(str(user.id))
    logger.info("User login")
    return LoginResponse(**tokens)


@router.get("/me", response_model=UserResponse)
async def api_me(
    db_session: Annotated[AsyncSession, Depends(get_auth_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> UserResponse:
    """获取当前用户信息"""
    logger.info("User get user info")
    user, groups, _ = await get_user(db_session, payload.sub, options="group")
    return UserResponse(username=user.name, email=user.email, groups=groups)


@router.post("/me/username", status_code=status.HTTP_202_ACCEPTED)
async def api_update_username(
    body: UpdateUsernameRequest,
    db_session: Annotated[AsyncSession, Depends(get_auth_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> None:
    """修改用户名"""
    logger.info("User update username")
    await update_username(db_session, payload.sub, body.username)


@router.post(
    "/me/email", status_code=status.HTTP_202_ACCEPTED, response_model=LoginResponse
)
async def api_update_email(
    body: UpdateEmailRequest,
    db_session: Annotated[AsyncSession, Depends(get_auth_db)],
    payload: Annotated[RefreshTokenPayload, Depends(authenticate_refresh_token)],
    response: Response,
) -> LoginResponse:
    """修改邮箱"""
    logger.info("User update email")
    # 修改邮箱
    await update_email(db_session, payload.sub, body.email)
    # 撤销用户所有刷新令牌
    await revoke_all_refresh_tokens(db_session, payload.sub)
    logger.info("User email updated, all refresh tokens revoked")
    # 登录
    user, tokens = await login_by_user_id(db_session, payload.sub, response)
    return LoginResponse(**tokens)


@router.post(
    "/me/password", status_code=status.HTTP_202_ACCEPTED, response_model=LoginResponse
)
async def api_update_password(
    body: UpdatePasswordRequest,
    db_session: Annotated[AsyncSession, Depends(get_auth_db)],
    payload: Annotated[RefreshTokenPayload, Depends(authenticate_refresh_token)],
    response: Response,
) -> LoginResponse:
    """修改密码"""
    logger.info("User update password")
    # 修改密码
    await update_password(db_session, payload.sub, body.password)
    # 撤销用户所有刷新令牌
    await revoke_all_refresh_tokens(db_session, payload.sub)
    logger.info("User password updated, all refresh tokens revoked")
    # 登录
    user, tokens = await login_by_user_id(db_session, payload.sub, response)
    return LoginResponse(**tokens)


@router.post("/refresh", response_model=LoginResponse)
async def api_refresh(
    db_session: Annotated[AsyncSession, Depends(get_auth_db)],
    payload: Annotated[RefreshTokenPayload, Depends(authenticate_refresh_token)],
    response: Response,
) -> LoginResponse:
    """刷新令牌"""
    logger.info("User refresh token")
    # 撤销旧的刷新令牌
    await revoke_refresh_token(db_session, payload.jti, payload.sub)
    # 登录
    user, tokens = await login_by_user_id(db_session, payload.sub, response)
    return LoginResponse(**tokens)


@router.post("/logout")
async def api_logout(
    db_session: Annotated[AsyncSession, Depends(get_auth_db)],
    payload: Annotated[RefreshTokenPayload, Depends(authenticate_refresh_token)],
) -> None:
    """用户登出"""
    logger.info("User logout")
    # 撤销旧的刷新令牌
    await revoke_refresh_token(db_session, payload.jti, payload.sub)


@router.post("/verify_access_token", response_model=AccessTokenPayload)
async def api_verify_access_token(
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> AccessTokenPayload:
    """验证访问令牌"""
    logger.info("Verify access token")
    return payload
