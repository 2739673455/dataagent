from typing import Callable

from app.utils.log import logger
from fastapi import Request, Response


async def middleware(request: Request, call_next: Callable) -> Response:
    """验证访问令牌"""

    response = await call_next(request)  # 执行请求
    return response
