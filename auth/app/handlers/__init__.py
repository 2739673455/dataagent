from app.exceptions.base import AppError
from app.utils.context import trace_id_ctx
from app.utils.log import logger
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _build_response(
    status_code: int, code: int, exc_type: str, message: str, detail: str | None = None
) -> JSONResponse:
    payload = {"code": code, "exc_type": exc_type, "message": message}
    if detail:
        payload["detail"] = detail
    trace_id = trace_id_ctx.get()
    if trace_id:
        payload["trace_id"] = trace_id
    return JSONResponse(status_code=status_code, content=payload)


def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
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
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
