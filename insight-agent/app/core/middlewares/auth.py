"""认证中间件 — 校验请求的 Authorization 头，将令牌载荷注入 Request.state"""

from collections.abc import Callable

from fastapi import Request, Response

from app.core import context
from app.core.exceptions.base import ProblemError
from app.core.exceptions.exc_handlers import problem_error_handler
from app.core.http_client import get_http_client
from app.core.settings import cfg
from app.errors import auth_error
from app.schemas import auth_schema

# 需要认证的请求路径前缀
AUTH_REQUIRED_PREFIXES = {"/api"}


async def authenticate_authorization(
    authorization: str | None,
) -> auth_schema.IntrospectionResponse:
    if not authorization:
        raise auth_error.MissingAccessTokenError()

    try:
        client = get_http_client()
        resp = await client.post(
            cfg.auth_service.base_url + cfg.auth_service.introspection,
            headers={"Authorization": authorization},
        )
    except Exception as e:
        raise auth_error.AuthServiceUnavailableError(detail=str(e)) from e

    if resp.status_code != 200:
        raise auth_error.AuthServiceResponseError(detail=resp.text)

    try:
        data = auth_schema.IntrospectionResponse.model_validate(resp.json())
    except Exception as e:
        raise auth_error.AuthServiceResponseError(detail=str(e)) from e

    if not data.active:
        raise auth_error.InvalidAccessTokenError()

    return data


async def middleware(request: Request, call_next: Callable) -> Response:
    # 获取请求路径
    path = request.url.path

    # 如果不是需要认证的路径，直接返回
    if not any(path.startswith(p) for p in AUTH_REQUIRED_PREFIXES):
        return await call_next(request)

    try:
        # 请求认证服务
        request.state.payload = await authenticate_authorization(
            request.headers.get("Authorization")
        )
        # 将用户 ID 放入 ContextVar
        context.user_id_ctx.set(str(request.state.payload.sub))
    except ProblemError as exc:
        return problem_error_handler(request, exc)

    return await call_next(request)
