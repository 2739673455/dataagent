from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Cookie, Form, Header, Query, Request
from fastapi.responses import RedirectResponse
from loguru import logger

from app.core.settings import AppCfg, CookieCfg

from . import auth_schema
from .auth_service import AuthService
from .deps import resolve_access_token_from_header


def create_router(
    app_config: AppCfg, cookie_config: CookieCfg, auth_service: AuthService
) -> APIRouter:
    """创建认证模块路由"""
    COOKIE_OPTIONS = {
        "secure": cookie_config.secure,
        "httponly": cookie_config.httponly,
        "samesite": cookie_config.samesite,
    }

    router = APIRouter()

    @router.get("/authorize")
    async def authorize(
        request: Request,
        client_id: Annotated[str, Query(min_length=1)],
        redirect_uri: Annotated[str, Query(min_length=1)],
        session_id: Annotated[str | None, Cookie(alias=cookie_config.name)] = None,
    ) -> RedirectResponse:
        """处理 OAuth 授权请求，必要时跳转登录"""
        base_url = f"{app_config.web_base_url or str(request.base_url).rstrip('/')}"
        login_url = f"{base_url}/login?{urlencode({'client_id': client_id, 'redirect_uri': redirect_uri})}"

        auth_result = await auth_service.create_authorization_code(
            session_id,
            client_id,
            redirect_uri,
        )
        if auth_result is None:
            response = RedirectResponse(url=login_url)
            response.delete_cookie(key=cookie_config.name, **COOKIE_OPTIONS)
            return response

        parsed_redirect_uri = urlparse(redirect_uri)
        redirect_query = dict(parse_qsl(parsed_redirect_uri.query))
        redirect_query["code"] = auth_result.code
        redirect_url = urlunparse(
            parsed_redirect_uri._replace(query=urlencode(redirect_query))
        )

        response = RedirectResponse(url=redirect_url)
        response.set_cookie(
            key=cookie_config.name,
            value=auth_result.session_id,
            max_age=auth_result.session_expire_seconds,
            **COOKIE_OPTIONS,
        )
        return response

    @router.post("/token")
    async def token(
        code: Annotated[str, Form(min_length=1)],
        client_id: Annotated[str, Form(min_length=1)],
        client_secret: Annotated[str | None, Form()] = None,
    ) -> auth_schema.TokenResponse:
        """授权码换取访问令牌"""
        access_token = await auth_service.exchange_token(code, client_id, client_secret)
        return auth_schema.TokenResponse(access_token=access_token)

    @router.post("/introspection")
    async def introspection(
        authorization: Annotated[str | None, Header()] = None,
    ) -> auth_schema.IntrospectionResponse:
        """校验访问令牌"""
        payload = await resolve_access_token_from_header(authorization)

        if payload is None:
            logger.info("访问令牌无效")
            return auth_schema.IntrospectionResponse(active=False)

        logger.info("访问令牌有效")
        return auth_schema.IntrospectionResponse(
            active=True,
            sub=payload.sub,
            exp=payload.exp,
            scope=payload.scope,
        )

    return router
