import time
import uuid
from typing import Callable

from app.utils import context
from fastapi import Request, Response
from loguru import logger


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

    start_time = time.time()
    error = None
    try:
        context.status_ctx.set("start")  # 设置 status 到 ContextVar
        logger.debug("Request incoming")
        context.status_ctx.set("processing")  # 设置 status 到 ContextVar
        response = await call_next(request)  # 执行请求
    except Exception as e:
        error = e
        raise
    finally:
        context.response_time_ms_ctx.set(
            round((time.time() - start_time) * 1000, 2)
        )  # 设置 response_time_ms 到 ContextVar
        if error:
            context.status_ctx.set("fail")  # 设置 status 到 ContextVar
        else:
            context.status_ctx.set("finish")  # 设置 status 到 ContextVar

            logger.debug("Request completed")

    # 添加请求ID和追踪ID到响应头
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Trace-ID"] = trace_id

    return response
