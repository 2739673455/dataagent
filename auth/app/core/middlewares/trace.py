import uuid
from collections.abc import Callable

from fastapi import Request, Response

from app.core import context


def _get_client_ip(request: Request) -> str:
    """获取 IP 地址"""
    if forwarded := request.headers.get("X-Forwarded-For"):
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def middleware(request: Request, call_next: Callable) -> Response:
    """追踪中间件"""
    # 生成请求ID和追踪ID
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    trace_id = request.headers.get("X-Trace-ID", request_id)

    # 将请求上下文放入 ContextVar
    context.request_id_ctx.set(request_id)
    context.trace_id_ctx.set(trace_id)
    context.client_ip_ctx.set(_get_client_ip(request))
    context.method_ctx.set(request.method)
    context.path_ctx.set(request.url.path)

    response = await call_next(request)  # 执行请求

    # 添加请求ID和追踪ID到响应头
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Trace-ID"] = trace_id

    return response
