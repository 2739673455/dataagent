import sys

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.assistant.api.attachment.router import router as attachment_router
from app.assistant.api.chat.router import router as chat_router
from app.identity.api.admin.router import router as admin_router
from app.identity.api.admin.task_router import router as task_router
from app.identity.api.auth.router import router as auth_router
from app.runtime import lifespan
from app.shared.config.app_config import cfg
from app.shared.errors.base import ProblemDetails
from app.shared.errors.exc_handlers import register_exception_handlers
from app.shared.observability import trace
from app.shared.observability.log import setup_logger

_PROBLEM_RESPONSE = {
    "model": ProblemDetails,
    "content": {
        "application/problem+json": {
            "schema": {"$ref": "#/components/schemas/ProblemDetails"}
        }
    },
}
_ERROR_RESPONSES = {
    422: {
        **_PROBLEM_RESPONSE,
        "description": "参数校验失败",
    },
    "default": {
        **_PROBLEM_RESPONSE,
        "description": "Problem Details 错误响应",
    },
}


def _register_routes(app: FastAPI) -> None:
    """注册接口。"""
    app.include_router(auth_router, prefix="/api/v1/auth")
    app.include_router(admin_router, prefix="/api/v1/admin")
    app.include_router(chat_router, prefix="/api/v1/chat")
    app.include_router(
        attachment_router,
        prefix="/api/v1/chat/attachment",
    )
    app.include_router(task_router, prefix="/api/v1/tasks")


def _register_middlewares(app: FastAPI) -> None:
    """注册中间件。"""
    app.middleware("http")(trace.middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _create_app() -> FastAPI:
    """创建并组装 FastAPI 应用。"""
    setup_logger()
    app = FastAPI(lifespan=lifespan, responses=_ERROR_RESPONSES)
    _register_middlewares(app)
    register_exception_handlers(app)
    _register_routes(app)
    return app


app = _create_app()


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=cfg.port,
        loop=(
            "app.shared.async_runtime:create_event_loop"
            if sys.platform == "win32"
            else "auto"
        ),
    )
