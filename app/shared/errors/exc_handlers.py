"""FastAPI 全局异常处理器"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException
from starlette.types import ExceptionHandler

from app.shared.errors.base import ProblemError


def _build_response(
    request: Request,
    exc: ProblemError,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """构造 Problem Details 错误响应"""
    return JSONResponse(
        status_code=exc.status,
        content=exc.to_problem(instance=request.url.path),
        headers=headers,
        media_type="application/problem+json",
    )


def _log_problem(exc: ProblemError, source: Exception) -> None:
    """按响应状态记录异常"""
    context = {
        "problem_type": exc.type,
        "status": exc.status,
        "exc_type": type(source).__name__,
        "detail": exc.detail,
    }
    if exc.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        logger.opt(exception=source).error(exc.title, **context)
    else:
        logger.warning(exc.title, **context)


def _problem_error_handler(request: Request, exc: ProblemError) -> JSONResponse:
    """处理应用预期异常"""
    _log_problem(exc, exc)
    retry_after = exc.extensions.get("retry_after_seconds")
    headers = (
        {"Retry-After": str(retry_after)}
        if exc.status == HTTPStatus.TOO_MANY_REQUESTS
        and isinstance(retry_after, int)
        and retry_after > 0
        else None
    )
    return _build_response(request, exc, headers=headers)


def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理请求参数校验异常"""
    errors: list[dict[str, Any]] = [
        {
            "type": error["type"],
            "location": list(error["loc"]),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    problem = ProblemError(
        title="参数校验失败",
        detail="请求参数不符合接口要求",
        type="validation-error",
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
        extensions={"errors": errors},
    )
    _log_problem(problem, exc)
    return _build_response(request, problem)


def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """统一处理 FastAPI 与 Starlette HTTP 异常"""
    if isinstance(exc.detail, str):
        detail = exc.detail
    else:
        detail = "请求处理失败"
    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        title = "HTTP 请求错误"
    problem = ProblemError(
        title=title,
        detail=detail,
        status=exc.status_code,
        type=f"http-{exc.status_code}",
        extensions=(
            {"errors": exc.detail} if isinstance(exc.detail, (list, dict)) else None
        ),
    )
    _log_problem(problem, exc)
    return _build_response(request, problem, headers=exc.headers)


def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理所有未捕获异常且不向客户端泄露内部信息"""
    problem = ProblemError()
    _log_problem(problem, exc)
    return _build_response(request, problem)


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器"""
    app.add_exception_handler(
        ProblemError,
        cast(ExceptionHandler, _problem_error_handler),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, _validation_error_handler),
    )
    app.add_exception_handler(
        HTTPException,
        cast(ExceptionHandler, _http_exception_handler),
    )
    app.add_exception_handler(
        Exception,
        cast(ExceptionHandler, _unhandled_exception_handler),
    )
