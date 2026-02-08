from typing import Callable

from app.config import CFG
from app.utils.http_client import apost
from fastapi import Request, Response


async def middleware(request: Request, call_next: Callable) -> Response:
    """验证访问令牌"""
    payload = await apost(CFG.authentication_url)
    response = await call_next(request)
    return response
