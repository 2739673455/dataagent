"""OAuth HTTP 路由"""

from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Cookie, Depends, Form, Header, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger

from app.application.oauth.use_cases import OAuthUseCases
from app.core.settings import AppCfg, CookieCfg
from app.domain.errors import InvalidAuthorizationRequestError
from app.domain.ports import TokenRepository
from app.presentation.deps import (
    AccessTokenPayload,
    authenticate_access_token,
    resolve_access_token_from_header,
)
from app.presentation.oauth.schemas import (
    IntrospectionResponse,
    LoginRequest,
    TokenResponse,
)


def _authorization_error_page() -> HTMLResponse:
    """授权请求无效时返回的中文错误页面"""
    return HTMLResponse(
        status_code=400,
        content="""\
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>授权请求无效</title>
  <style>
    body { margin: 0; height: 100vh; display: flex; flex-direction: column;
           justify-content: center; align-items: center; text-align: center;
           background: #f8f9fa; font-family: sans-serif; }
    h1 { color: #f44336; font-size: 30px; margin: 0 0 20px; }
    p { font-size: 20px; color: #444; margin: 0; line-height: 1.8; }
  </style>
</head>
<body>
    <h1>授权请求无效</h1>
    <p>授权请求已过期或不正确，<br>请返回应用重新发起登录。</p>
</body>
</html>""",
    )


def create_router(
    app_config: AppCfg,
    cookie_config: CookieCfg,
    oauth_use_cases: OAuthUseCases,
    token_repo: TokenRepository,
) -> APIRouter:
    cookie_options = {
        "secure": cookie_config.secure,
        "httponly": cookie_config.httponly,
        "samesite": cookie_config.samesite,
    }
    _authenticate = authenticate_access_token(token_repo)

    router = APIRouter()

    @router.get("/authorize")
    async def authorize(
        request: Request,
        response_type: Annotated[str | None, Query()] = None,
        client_id: Annotated[str | None, Query()] = None,
        redirect_uri: Annotated[str | None, Query()] = None,
        state: Annotated[str | None, Query()] = None,
        code_challenge: Annotated[str | None, Query()] = None,
        code_challenge_method: Annotated[str | None, Query()] = None,
        session_id: Annotated[str | None, Cookie(alias=cookie_config.name)] = None,
    ) -> Response:
        """OAuth 授权端点：校验请求并重定向（含授权码或回登录页）"""
        base_url = app_config.web_base_url or str(request.base_url).rstrip("/")
        auth_params = {
            k: v
            for k, v in {
                "response_type": response_type,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
            }.items()
            if v is not None
        }
        login_url = f"{base_url}/login?{urlencode(auth_params)}"

        try:
            result = await oauth_use_cases.authorize.execute(
                session_id,
                response_type,
                client_id,
                redirect_uri,
                state,
                code_challenge,
                code_challenge_method,
            )
        except InvalidAuthorizationRequestError:
            logger.exception("授权请求无效")
            return _authorization_error_page()

        if result is None:
            logger.info("无登录会话，重定向到登录页")
            resp = RedirectResponse(url=login_url)
            resp.delete_cookie(key=cookie_config.name, **cookie_options)
            return resp

        parsed = urlparse(result.redirect_uri)
        qs = dict(parse_qsl(parsed.query))
        qs["code"] = result.code
        qs["state"] = result.state
        redirect_url = urlunparse(parsed._replace(query=urlencode(qs)))

        resp = RedirectResponse(url=redirect_url)
        resp.set_cookie(
            key=cookie_config.name,
            value=result.session_id,
            max_age=result.session_expire_seconds,
            **cookie_options,
        )
        return resp

    @router.post("/token")
    async def token(
        grant_type: Annotated[str, Form(min_length=1)],
        code: Annotated[str, Form(min_length=1)],
        client_id: Annotated[str, Form(min_length=1)],
        redirect_uri: Annotated[str, Form(min_length=1)],
        code_verifier: Annotated[str, Form(min_length=1)],
    ) -> TokenResponse:
        """OAuth 令牌端点：授权码 + PKCE 换取 access token"""
        result = await oauth_use_cases.exchange_token.execute(
            grant_type,
            code,
            client_id,
            redirect_uri,
            code_verifier,
        )
        return TokenResponse(access_token=result.access_token)

    @router.post("/introspection")
    async def introspection(
        authorization: Annotated[str | None, Header()] = None,
    ) -> IntrospectionResponse:
        """令牌内省端点：校验 access token 是否有效"""
        payload = await resolve_access_token_from_header(authorization, token_repo)
        if payload is None:
            logger.info("访问令牌无效")
            return IntrospectionResponse(active=False)
        return IntrospectionResponse(
            active=True,
            sub=payload.sub,
            exp=payload.exp,
            scope=payload.scope,
        )

    @router.post("/login")
    async def login(body: LoginRequest, response: Response) -> None:
        """OAuth 登录：校验凭证并设置会话 Cookie"""
        session_data = await oauth_use_cases.login.execute(body.email, body.password)
        logger.info("登录成功")
        response.set_cookie(
            key=cookie_config.name,
            value=session_data.session_id,
            max_age=session_data.session_expire_seconds,
            **cookie_options,
        )

    @router.post("/logout")
    async def logout(
        response: Response,
        payload: Annotated["AccessTokenPayload", Depends(_authenticate)],
        session_id: Annotated[str | None, Cookie(alias=cookie_config.name)] = None,
    ) -> None:
        """OAuth 登出：清除令牌和会话 Cookie"""
        await oauth_use_cases.logout.execute(payload.access_token, session_id)
        response.delete_cookie(key=cookie_config.name, **cookie_options)

    return router
