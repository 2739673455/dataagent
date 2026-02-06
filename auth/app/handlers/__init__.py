from app.exceptions.base import AppError
from app.utils.context import trace_id_ctx
from app.utils.log import logger
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _build_response(
    status_code: int,
    code: int,
    message: str,
    detail: str | None = None,
    exc_type: str | None = None,
) -> JSONResponse:
    payload = {"code": code, "message": message}
    if detail:
        payload["detail"] = detail
    trace_id = trace_id_ctx.get()
    if trace_id:
        payload["trace_id"] = trace_id
    logger.warning(exc_type or "Response", **payload)
    return JSONResponse(status_code=status_code, content=payload)


def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _build_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        detail=exc.detail,
        exc_type=type(exc).__name__,
    )


def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = [
        {"type": e["type"], "loc": e["loc"], "msg": e["msg"]} for e in exc.errors()
    ]
    return _build_response(
        status_code=422,
        code=422,
        message="参数校验失败",
        detail=str(errors),
        exc_type="ValidationError",
    )


def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "请求错误"
    detail = exc.detail if not isinstance(exc.detail, str) else None
    return _build_response(
        status_code=exc.status_code,
        code=exc.status_code,
        message=message,
        detail=detail,
        exc_type=type(exc).__name__,
    )


def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(type(exc).__name__, exc_info=exc)
    return _build_response(
        status_code=500,
        code=500,
        message="内部服务器错误",
        exc_type="InternalServerError",
    )


def register_exception_handlers(app) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
