from typing import Callable

from app.config import CFG
from fastapi import Request, Response
from fastapi.responses import JSONResponse


async def middleware(request: Request, call_next: Callable) -> Response:
    """验证访问令牌"""
    try:
        # 请求远程服务验证访问令牌
        pass
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
