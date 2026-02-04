from app.exceptions.base import AppError, InternalServerError, ValidationError
from app.utils.context import trace_id_ctx
from app.utils.log import logger
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


# 统一构造应用错误响应
def _response(error: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_dict(trace_id_ctx.get()),
    )


# 应用自定义错误处理
def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning("AppError", error=str(exc), code=exc.code)
    return _response(exc)


# 参数校验错误处理
def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    err = ValidationError(detail=exc.errors())
    logger.warning("ValidationError", error=str(exc))
    return _response(err)


# HTTPException 错误处理
def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    trace_id = trace_id_ctx.get()
    message = exc.detail if isinstance(exc.detail, str) else "请求错误"
    detail = exc.detail if not isinstance(exc.detail, str) else None
    payload = {
        "code": exc.status_code,
        "message": message,
    }
    if detail is not None:
        payload["detail"] = detail
    if trace_id:
        payload["trace_id"] = trace_id
    logger.warning("HTTPException", status_code=exc.status_code, detail=exc.detail)
    return JSONResponse(status_code=exc.status_code, content=payload)


# 未捕获异常处理
def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception", exc_info=exc)
    return _response(InternalServerError())


# 注册 FastAPI 全局异常处理器
def register_exception_handlers(app) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
