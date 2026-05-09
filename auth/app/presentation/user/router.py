"""User HTTP 路由"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.application.user.use_cases import UserUseCases
from app.core.settings import CookieCfg
from app.domain.ports import TokenRepository
from app.presentation.deps import AccessTokenPayload, authenticate_access_token
from app.presentation.user.schemas import (
    RegisterRequest,
    SendCodeRequest,
    UpdateEmailRequest,
    UpdatePasswordRequest,
    UpdateUsernameRequest,
    UserResponse,
)


def create_router(
    cookie_config: CookieCfg,
    user_use_cases: UserUseCases,
    token_repo: TokenRepository,
) -> APIRouter:
    cookie_options = {
        "secure": cookie_config.secure,
        "httponly": cookie_config.httponly,
        "samesite": cookie_config.samesite,
    }
    _authenticate = authenticate_access_token(token_repo)

    router = APIRouter()

    @router.post("/send_email_code")
    async def send_email_code(body: SendCodeRequest) -> None:
        """发送邮箱验证码"""
        await user_use_cases.send_email_code.execute(body.email, body.type)

    @router.post("/register")
    async def register(body: RegisterRequest, response: Response) -> None:
        """用户注册：验证码 + 创建账户 + 设置会话 Cookie"""
        session_data = await user_use_cases.register_user.execute(
            body.email,
            body.code,
            body.username,
            body.password,
        )
        response.set_cookie(
            key=cookie_config.name,
            value=session_data.session_id,
            max_age=session_data.session_expire_seconds,
            **cookie_options,
        )

    @router.post("/update_username")
    async def update_username(
        body: UpdateUsernameRequest,
        payload: Annotated[AccessTokenPayload, Depends(_authenticate)],
    ) -> None:
        """修改用户名（需认证）"""
        await user_use_cases.update_username.execute(payload.sub, body.username)

    @router.post("/update_email")
    async def update_email(
        body: UpdateEmailRequest,
        payload: Annotated[AccessTokenPayload, Depends(_authenticate)],
    ) -> None:
        """修改邮箱（需认证 + 验证码）"""
        await user_use_cases.update_email.execute(payload.sub, body.email, body.code)

    @router.post("/update_password")
    async def update_password(body: UpdatePasswordRequest) -> None:
        """重置密码（需验证码）"""
        await user_use_cases.reset_password.execute(body.email, body.code, body.password)

    @router.get("/userinfo")
    async def userinfo(
        payload: Annotated[AccessTokenPayload, Depends(_authenticate)],
    ) -> UserResponse:
        """获取当前用户信息（需认证）"""
        result = await user_use_cases.get_current_user_info.execute(payload.sub)
        return UserResponse(username=result.username, email=result.email, roles=result.roles)

    return router
