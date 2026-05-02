"""认证中间件 — 校验请求的 Authorization 头，将令牌载荷注入 Request.state"""

from typing import Callable

from app.core import context
from app.core.exceptions.base import ProblemError
from app.core.exceptions.exc_handlers import problem_error_handler
from app.core.http_client import get_http_client
from app.core.settings import cfg
from app.exceptions import auth_error
from app.schemas import auth_schema
from fastapi import Request, Response

# 需要认证的请求路径前缀
AUTH_REQUIRED_PREFIXES = {"/api"}


async def authenticate_authorization(
    authorization: str | None,
) -> auth_schema.AccessTokenPayload:
    if not authorization:
        raise auth_error.MissingAccessTokenError()

    try:
        client = get_http_client()
        resp = await client.post(
            cfg.auth_service.base_url + cfg.auth_service.introspection,
            headers={"Authorization": authorization},
        )
    except Exception as e:
        raise auth_error.AuthServiceUnavailableError(detail=str(e))

    if resp.status_code != 200:
        raise auth_error.AuthServiceResponseError(detail=resp.text)

    try:
        data = auth_schema.IntrospectionResponse.model_validate(resp.json())
    except Exception as e:
        raise auth_error.AuthServiceResponseError(detail=str(e))

    if not data.active:
        raise auth_error.InvalidAccessTokenError()

    return data.to_payload()


async def middleware(request: Request, call_next: Callable) -> Response:
    path = request.url.path

    if not any(path.startswith(p) for p in AUTH_REQUIRED_PREFIXES):
        return await call_next(request)

    try:
        request.state.payload = await authenticate_authorization(
            request.headers.get("Authorization")
        )
        context.user_id_ctx.set(str(request.state.payload.sub))
    except ProblemError as exc:
        return problem_error_handler(request, exc)

    return await call_next(request)
