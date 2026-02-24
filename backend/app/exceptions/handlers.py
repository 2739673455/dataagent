from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.exceptions.base import AppError
from app.utils.context import trace_id_ctx
from app.utils.log import logger


def _build_response(
    status_code: int, code: int, exc_type: str, message: str, detail: str | None = None
) -> JSONResponse:
    """构造统一的错误响应 JSON"""
    payload = {"code": code, "exc_type": exc_type, "message": message}
    if detail:
        payload["detail"] = detail
    trace_id = trace_id_ctx.get()
    if trace_id:
        payload["trace_id"] = trace_id
    return JSONResponse(status_code=status_code, content=payload)


def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """处理 AppError 及其子类异常"""
    status_code = exc.status_code
    code = exc.code
    exc_type = type(exc).__name__
    message = exc.message
    detail = exc.detail

    logger.warning(message, code=code, exc_type=exc_type, detail=detail)
    return _build_response(
        status_code=status_code,
        code=code,
        exc_type=exc_type,
        message=message,
        detail=detail,
    )


def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理 Pydantic 参数校验错误"""
    status_code = 422
    code = 422
    exc_type = "ValidationError"
    message = "参数校验失败"
    detail = str(
        [{"type": e["type"], "loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
    )

    logger.warning(message, code=code, exc_type=exc_type, detail=detail)
    return _build_response(
        status_code=status_code,
        code=code,
        exc_type=exc_type,
        message=message,
        detail=detail,
    )


def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """处理 FastAPI 原生 HTTPException 异常"""
    status_code = exc.status_code
    code = exc.status_code
    exc_type = type(exc).__name__
    message = exc.detail if isinstance(exc.detail, str) else "请求错误"
    detail = exc.detail if not isinstance(exc.detail, str) else None

    logger.warning(message, code=code, exc_type=exc_type, detail=detail)
    return _build_response(
        status_code=status_code,
        code=code,
        exc_type=exc_type,
        message=message,
        detail=detail,
    )


def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理所有未捕获的异常"""
    status_code = 500
    code = 500
    exc_type = type(exc).__name__
    message = "内部服务器错误"
    detail = str(exc)

    logger.exception(message, code=code, exc_type=exc_type, detail=detail)
    return _build_response(
        status_code=status_code,
        code=code,
        exc_type="InternalServerError",
        message=message,
    )


def register_exception_handlers(app) -> None:
    """向 FastAPI 应用注册全局异常处理器"""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
