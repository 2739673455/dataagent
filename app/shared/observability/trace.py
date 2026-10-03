"""HTTP 请求追踪标识与日志上下文中间件。"""

import uuid
from collections.abc import Callable

from fastapi import Request, Response

from app.shared.observability import context


async def middleware(request: Request, call_next: Callable) -> Response:
    """为请求绑定追踪上下文，在响应中返回追踪标识并清理上下文。"""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    trace_id = request.headers.get("X-Trace-ID", request_id)
    request_id_token = context.request_id_ctx.set(request_id)
    trace_id_token = context.trace_id_ctx.set(trace_id)
    client_ip_token = context.client_ip_ctx.set(_get_client_ip(request))
    method_token = context.method_ctx.set(request.method)
    path_token = context.path_ctx.set(request.url.path)
    user_id_token = context.user_id_ctx.set(None)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = trace_id
        return response
    finally:
        # 请求结束时恢复 ContextVar，防止复用任务将身份和追踪信息带入下一请求。
        context.user_id_ctx.reset(user_id_token)
        context.path_ctx.reset(path_token)
        context.method_ctx.reset(method_token)
        context.client_ip_ctx.reset(client_ip_token)
        context.trace_id_ctx.reset(trace_id_token)
        context.request_id_ctx.reset(request_id_token)


def _get_client_ip(request: Request) -> str:
    """获取 IP 地址。"""
    # 日志上下文记录转发地址；认证限流以 ASGI peer 地址作为可信来源。
    if forwarded := request.headers.get("X-Forwarded-For"):
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
