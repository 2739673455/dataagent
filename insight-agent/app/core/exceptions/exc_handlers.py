from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.exceptions.base import (
    InternalServerError,
    ProblemError,
    ValidationError,
)


def _build_response(request: Request, exc: ProblemError) -> JSONResponse:
    """构造错误响应"""
    payload = exc.to_problem(instance=str(request.url))
    return JSONResponse(
        status_code=exc.status,
        content=payload,
        media_type="application/problem+json",
    )


def problem_error_handler(request: Request, exc: ProblemError) -> JSONResponse:
    """处理 ProblemError 及其子类异常"""
    exc_type = type(exc).__name__

    logger.warning(
        exc.title,
        problem_type=exc.type,
        status=exc.status,
        exc_type=exc_type,
        detail=exc.detail,
    )
    return _build_response(request, exc)


def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """处理 Pydantic 参数校验错误"""
    errors = [e["msg"] for e in exc.errors()]
    detail = errors[0] if len(errors) == 1 else str(errors)
    problem = ValidationError(detail=detail)

    logger.warning(
        problem.title,
        problem_type=problem.type,
        status=problem.status,
        exc_type="ValidationError",
        detail=problem.detail,
    )
    return _build_response(request, problem)


def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """处理 FastAPI 原生 HTTPException 异常"""
    title = exc.detail if isinstance(exc.detail, str) else "请求错误"
    detail = None if isinstance(exc.detail, str) else str(exc.detail)
    problem = ProblemError(
        title,
        detail=detail,
        status=exc.status_code,
        type=f"http-{exc.status_code}",
    )

    logger.warning(
        problem.title,
        problem_type=problem.type,
        status=problem.status,
        exc_type=type(exc).__name__,
        detail=problem.detail,
    )
    return _build_response(request, problem)


def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理所有未捕获的异常"""
    detail = str(exc)
    problem = InternalServerError()

    logger.exception(
        problem.title,
        problem_type=problem.type,
        status=problem.status,
        exc_type=type(exc).__name__,
        detail=detail,
    )
    return _build_response(request, problem)
