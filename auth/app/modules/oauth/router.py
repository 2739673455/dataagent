from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.core.settings import AppCfg, CookieCfg
from fastapi import APIRouter, Cookie, Depends, Form, Header, Query, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger

from ..shared import errors
from ..shared.deps import (
    authenticate_access_token,
    resolve_access_token_from_header,
)
from ..shared.schemas import AccessTokenPayload
from . import oauth_schema
from .oauth_service import OAuthService


def create_router(
    app_config: AppCfg, cookie_config: CookieCfg, oauth_service: OAuthService
) -> APIRouter:
    """创建 OAuth 模块路由"""
    COOKIE_OPTIONS = {
        "secure": cookie_config.secure,
        "httponly": cookie_config.httponly,
        "samesite": cookie_config.samesite,
    }

    router = APIRouter()

    def authorization_error_page() -> HTMLResponse:
        """授权错误页面"""
        return HTMLResponse(
            status_code=400,
            content="""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>授权请求无效</title>
  <style>
    body {
      margin: 0;
      height: 100vh;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      text-align: center;
      background: #f8f9fa;
      font-family: sans-serif;
    }
    h1 {
      color: #f44336;
      font-size: 30px;
      margin: 0 0 20px;
    }
    p {
      font-size: 20px;
      color: #444;
      margin: 0;
      line-height: 1.8;
    }
  </style>
</head>
<body>
    <h1>授权请求无效</h1>
    <p>授权请求已过期或不正确，<br>请返回应用重新发起登录。</p>
</body>
</html>
""".strip(),
        )

    @router.get("/authorize")
    async def authorize(
        response_type: Annotated[str | None, Query()] = None,
        client_id: Annotated[str | None, Query()] = None,
        redirect_uri: Annotated[str | None, Query()] = None,
        state: Annotated[str | None, Query()] = None,
        code_challenge: Annotated[str | None, Query()] = None,
        code_challenge_method: Annotated[str | None, Query()] = None,
        session_id: Annotated[str | None, Cookie(alias=cookie_config.name)] = None,
    ) -> Response:
        """OAuth 授权端点 — 已登录则返回授权码，否则跳转登录页"""
        # 拼接未登录时的跳转 URL
        base_url = app_config.web_base_url
        auth_params = {
            key: value
            for key, value in {
                "response_type": response_type,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
            }.items()
            if value is not None
        }
        login_url = f"{base_url}/login?{urlencode(auth_params)}"

        # 校验授权请求，已登录则签发授权码
        try:
            oauth_result = await oauth_service.create_authorization_code(
                session_id,
                response_type,
                client_id,
                redirect_uri,
                state,
                code_challenge,
                code_challenge_method,
            )
        except errors.InvalidAuthorizationRequestError:
            return authorization_error_page()

        # 未登录 — 跳转登录页
        if oauth_result is None:
            response = RedirectResponse(url=login_url)
            response.delete_cookie(key=cookie_config.name, **COOKIE_OPTIONS)
            return response

        # 已登录 — 将授权码拼回 redirect_uri 并跳转
        parsed_redirect_uri = urlparse(oauth_result.redirect_uri)
        redirect_query = dict(parse_qsl(parsed_redirect_uri.query))
        redirect_query["code"] = oauth_result.code
        redirect_query["state"] = oauth_result.state
        redirect_url = urlunparse(
            parsed_redirect_uri._replace(query=urlencode(redirect_query))
        )

        response = RedirectResponse(url=redirect_url)
        response.set_cookie(
            key=cookie_config.name,
            value=oauth_result.session_id,
            max_age=oauth_result.session_expire_seconds,
            **COOKIE_OPTIONS,
        )
        return response

    @router.post("/token")
    async def token(
        grant_type: Annotated[str, Form(min_length=1)],
        code: Annotated[str, Form(min_length=1)],
        client_id: Annotated[str, Form(min_length=1)],
        redirect_uri: Annotated[str, Form(min_length=1)],
        code_verifier: Annotated[str, Form(min_length=1)],
    ) -> oauth_schema.TokenResponse:
        """令牌端点 — 授权码换取访问令牌"""
        access_token = await oauth_service.exchange_token(
            grant_type,
            code,
            client_id,
            redirect_uri,
            code_verifier,
        )
        return oauth_schema.TokenResponse(access_token=access_token)

    @router.post("/introspection")
    async def introspection(
        authorization: Annotated[str | None, Header()] = None,
    ) -> oauth_schema.IntrospectionResponse:
        """令牌自省端点 — 校验访问令牌是否有效"""
        # 从 Authorization header 解析并校验令牌
        payload = await resolve_access_token_from_header(authorization)

        if payload is None:
            logger.info("访问令牌无效")
            return oauth_schema.IntrospectionResponse(active=False)

        logger.info("访问令牌有效")
        return oauth_schema.IntrospectionResponse(
            active=True,
            sub=payload.sub,
            exp=payload.exp,
            scope=payload.scope,
        )

    @router.post("/login")
    async def login(
        body: oauth_schema.LoginRequest,
        response: Response,
    ) -> None:
        """用户登录 — 创建会话并设置 Cookie"""
        # 校验邮箱密码，创建会话
        session_data = await oauth_service.login(body.email, body.password)
        # 设置会话 Cookie
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
        """用户登出 — 撤销令牌和会话，清除 Cookie"""
        # 撤销访问令牌和会话
        await oauth_service.logout(payload.access_token, session_id)
        # 清除会话 Cookie
        response.delete_cookie(
            key=cookie_config.name,
            **COOKIE_OPTIONS,
        )

    return router
