import uuid
from collections.abc import Callable

from fastapi import Request, Response

from app.shared.observability import context


def _get_client_ip(request: Request) -> str:
    """获取 IP 地址"""
    # 转发头只进入日志上下文；认证限流使用 ASGI peer 地址，不能信任客户端自报值。
    if forwarded := request.headers.get("X-Forwarded-For"):
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def middleware(request: Request, call_next: Callable) -> Response:
    """追踪中间件"""
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
        # ContextVar 值会被下游任务继承；请求结束时必须恢复调用方上下文，避免
        # 测试或替代 ASGI 调度器复用同一任务时把身份和追踪信息带入下一请求。
        context.user_id_ctx.reset(user_id_token)
        context.path_ctx.reset(path_token)
        context.method_ctx.reset(method_token)
        context.client_ip_ctx.reset(client_ip_token)
        context.trace_id_ctx.reset(trace_id_token)
        context.request_id_ctx.reset(request_id_token)
