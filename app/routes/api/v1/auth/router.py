"""用户认证与令牌管理路由"""

from fastapi import APIRouter, Request, Response, status

from app.routes.api.v1.auth import schemas
from app.routes.api.v1.auth.dependencies import (
    AuthRateLimitServiceDep,
    AuthServiceDep,
    CurrentUserDep,
    get_client_ip,
)

router = APIRouter(tags=["auth"])


@router.post("/login", response_model=schemas.TokenResponse)
async def login(
    body: schemas.LoginRequest,
    service: AuthServiceDep,
    rate_limit: AuthRateLimitServiceDep,
    request: Request,
) -> schemas.TokenResponse:
    """使用用户名或邮箱登录"""
    await rate_limit.check_login(get_client_ip(request), body.identifier)
    user, token_pair = await service.login(
        body.identifier,
        body.password.get_secret_value(),
    )
    return schemas.TokenResponse.from_result(user, token_pair)


@router.post("/refresh", response_model=schemas.TokenResponse)
async def refresh(
    body: schemas.RefreshRequest,
    service: AuthServiceDep,
    rate_limit: AuthRateLimitServiceDep,
    request: Request,
) -> schemas.TokenResponse:
    """轮换刷新令牌"""
    await rate_limit.check_refresh(get_client_ip(request))
    user, token_pair = await service.refresh(body.refresh_token)
    return schemas.TokenResponse.from_result(user, token_pair)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: schemas.LogoutRequest, service: AuthServiceDep) -> Response:
    """吊销刷新令牌族并退出登录"""
    await service.logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: schemas.ChangePasswordRequest,
    current_user: CurrentUserDep,
    service: AuthServiceDep,
) -> Response:
    """修改当前用户密码并使既有令牌立即失效"""
    await service.change_password(
        current_user.id,
        body.current_password.get_secret_value(),
        body.new_password.get_secret_value(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=schemas.UserResponse)
async def me(current_user: CurrentUserDep) -> schemas.UserResponse:
    """读取当前用户信息"""
    return schemas.UserResponse.from_user(current_user)
