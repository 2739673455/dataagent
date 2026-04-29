from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, Response

from app.core.settings import CookieCfg

from ..auth.auth_error import InvalidAccessTokenError
from ..auth.auth_schema import AccessTokenPayload
from ..auth.deps import resolve_access_token_from_header
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

    async def authenticate_access_token(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AccessTokenPayload:
        payload = await resolve_access_token_from_header(authorization)
        if payload is None:
            raise InvalidAccessTokenError
        return payload

    @router.post("/send_email_code")
    async def send_email_code(body: user_schema.SendCodeRequest) -> None:
        """发送邮箱验证码"""
        await user_service.send_email_code(body.email, body.type)

    @router.post("/register")
    async def register(
        body: user_schema.RegisterRequest,
        response: Response,
    ) -> None:
        """用户注册"""
        session_data = await user_service.register(
            body.email, body.code, body.username, body.password
        )
        response.set_cookie(
            key=cookie_config.name,
            value=session_data.session_id,
            max_age=session_data.session_expire_seconds,
            **COOKIE_OPTIONS,
        )

    @router.post("/login")
    async def login(
        body: user_schema.LoginRequest,
        response: Response,
    ) -> None:
        """用户登录"""
        session_data = await user_service.login(body.email, body.password)
        response.set_cookie(
            key=cookie_config.name,
            value=session_data.session_id,
            max_age=session_data.session_expire_seconds,
            **COOKIE_OPTIONS,
        )

    @router.post("/logout")
    async def logout(
        response: Response,
        payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
        session_id: Annotated[str | None, Cookie(alias=cookie_config.name)] = None,
    ) -> None:
        """登出"""
        await user_service.logout(payload.jti, session_id)
        response.delete_cookie(
            key=cookie_config.name,
            **COOKIE_OPTIONS,
        )

    @router.post("/update_username")
    async def update_username(
        body: user_schema.UpdateUsernameRequest,
        payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
    ) -> None:
        """修改用户名"""
        await user_service.update_username(payload.sub, body.username)

    @router.post("/update_email")
    async def update_email(
        body: user_schema.UpdateEmailRequest,
        payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
    ) -> None:
        """修改邮箱"""
        await user_service.update_email(payload.sub, body.email, body.code)

    @router.post("/update_password")
    async def update_password(body: user_schema.UpdatePasswordRequest) -> None:
        """修改密码（通过邮箱验证码重置，无需登录）"""
        await user_service.update_password(body.email, body.code, body.password)

    @router.get("/me")
    async def me(
        payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
    ) -> user_schema.UserResponse:
        """获取当前用户信息"""
        return await user_service.get_me(payload.sub)

    return router
