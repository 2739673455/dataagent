from typing import Callable

from app.config import CFG
from app.utils.http_client import apost
from fastapi import Request, Response


async def middleware(request: Request, call_next: Callable) -> Response:
    """验证访问令牌"""
    # 请求远程服务验证
    resp = await apost(
        CFG.verify_access_token_url, headers=request.headers.get("Authorization")
    )
    if resp.status_code == 200:
        payload = resp.json()
        request.state.payload = payload
        return await call_next(request)
    else:
        return
