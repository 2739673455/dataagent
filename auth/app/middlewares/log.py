import time
import uuid
from typing import Callable

from fastapi import Request, Response

from app.utils.context import (
    client_ip_ctx,
    method_ctx,
    path_ctx,
    request_id_ctx,
    response_time_ms_ctx,
    status_ctx,
    trace_id_ctx,
)
from app.utils.log import app_logger


def _get_client_ip(request: Request) -> str:
    """获取 IP 地址"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def log_middleware(request: Request, call_next: Callable) -> Response:
    """日志中间件"""
    # 生成请求ID和追踪ID
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    trace_id = request.headers.get("X-Trace-ID", request_id)

    # 将请求上下文放入 ContextVar
    request_id_ctx.set(request_id)
    trace_id_ctx.set(trace_id)
    client_ip_ctx.set(_get_client_ip(request))
    method_ctx.set(request.method)
    path_ctx.set(request.url.path)

    start_time = time.time()
    error = None
    try:
        status_ctx.set("start")  # 设置 status 到 ContextVar
        app_logger.debug("Request incoming")
        status_ctx.set("processing")  # 设置 status 到 ContextVar
        response = await call_next(request)  # 执行请求
    except Exception as e:
        error = str(e)
        raise
    finally:
        response_time_ms_ctx.set(
            round((time.time() - start_time) * 1000, 2)
        )  # 设置 response_time_ms 到 ContextVar
        if error:
            status_ctx.set("fail")  # 设置 status 到 ContextVar
            app_logger.debug(f"Request failed - {error}")
        else:
            status_ctx.set("finish")  # 设置 status 到 ContextVar
            app_logger.debug("Request completed")

    # 添加请求ID和追踪ID到响应头
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Trace-ID"] = trace_id

    return response
