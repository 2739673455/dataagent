from typing import Callable

from app.config import CFG
from app.utils.http_client import apost
from fastapi import Request, Response
from fastapi.responses import JSONResponse


async def middleware(request: Request, call_next: Callable) -> Response:
    """验证访问令牌"""
    try:
        # 请求远程服务验证访问令牌
        resp = await apost(
            f"{CFG.auth_service.base_url}{CFG.auth_service.verify_access_token}",
            headers={"Authorization": request.headers.get("Authorization")},
        )
        if resp.status_code == 200:
            payload = resp.json()
            request.state.payload = payload
            return await call_next(request)
        else:
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={
                "code": 502,
                "exc_type": "BadGateway",
                "message": "认证服务不可用",
                "detail": str(e),
            },
        )
