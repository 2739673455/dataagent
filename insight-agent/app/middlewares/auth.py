from typing import Callable

from app.config import CFG
from app.exceptions import auth as auth_error
from app.schemas import auth as auth_schema
from app.utils.http_client import get_http_client
from fastapi import Request, Response

# 不需要验证访问令牌的路径
AUTH_EXCLUDE_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


async def authenticate_authorization(
    authorization: str | None,
) -> auth_schema.AccessTokenPayload:
    """校验 Authorization 头并返回访问令牌载荷"""
    if not authorization:
        raise auth_error.MissingAccessTokenError

    try:
        client = get_http_client()
        resp = await client.post(
            CFG.auth_service.base_url + CFG.auth_service.introspection,
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
        raise auth_error.InvalidAccessTokenError

    return data.to_payload()


async def middleware(request: Request, call_next: Callable) -> Response:
    """验证访问令牌"""
    if request.url.path in AUTH_EXCLUDE_PATHS:
        return await call_next(request)

    request.state.payload = await authenticate_authorization(
        request.headers.get("Authorization")
    )
    return await call_next(request)
