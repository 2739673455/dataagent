from typing import Annotated

from app.core.settings import CookieCfg
from fastapi import APIRouter, Depends, Response

from ..shared.deps import authenticate_access_token
from ..shared.schemas import AccessTokenPayload
from . import user_schema
from .user_service import UserService


def create_router(
    cookie_config: CookieCfg,
    user_service: UserService,
) -> APIRouter:
    """创建用户模块路由"""
    COOKIE_OPTIONS = {
        "secure": cookie_config.secure,
        "httponly": cookie_config.httponly,
        "samesite": cookie_config.samesite,
    }

    router = APIRouter()

    @router.post("/send_email_code")
    async def send_email_code(body: user_schema.SendCodeRequest) -> None:
        """发送邮箱验证码"""
        # 校验邮箱是否可用，生成验证码并发送邮件
        await user_service.send_email_code(body.email, body.type)

    @router.post("/register")
    async def register(
        body: user_schema.RegisterRequest,
        response: Response,
    ) -> None:
        """用户注册"""
        # 校验验证码，创建用户和会话
        session_data = await user_service.register(
            body.email, body.code, body.username, body.password
        )
        # 设置会话 Cookie
        response.set_cookie(
            key=cookie_config.name,
            value=session_data.session_id,
            max_age=session_data.session_expire_seconds,
            **COOKIE_OPTIONS,
        )

    @router.post("/update_username")
    async def update_username(
        body: user_schema.UpdateUsernameRequest,
        payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
    ) -> None:
        """修改用户名"""
        # 校验用户状态，更新用户名
        await user_service.update_username(payload.sub, body.username)

    @router.post("/update_email")
    async def update_email(
        body: user_schema.UpdateEmailRequest,
        payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
    ) -> None:
        """修改邮箱"""
        # 校验验证码和用户状态，更新邮箱并撤销所有令牌
        await user_service.update_email(payload.sub, body.email, body.code)

    @router.post("/update_password")
    async def update_password(body: user_schema.UpdatePasswordRequest) -> None:
        """修改密码（通过邮箱验证码重置，无需登录）"""
        # 校验验证码和用户状态，更新密码并撤销所有会话和令牌
        await user_service.update_password(body.email, body.code, body.password)

    @router.get("/userinfo")
    async def userinfo(
        payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
    ) -> user_schema.UserResponse:
        """获取当前用户信息"""
        # 查询用户及其角色，组装响应
        return await user_service.get_userinfo(payload.sub)

    return router
