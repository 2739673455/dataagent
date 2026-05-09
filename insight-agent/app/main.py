from contextlib import asynccontextmanager
from typing import cast

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ExceptionHandler

from app import routers
from app.core import middlewares
from app.core.database import close_db
from app.core.exceptions import base, exc_handlers
from app.core.http_client import close_http_client
from app.core.log_setup import setup_logger
from app.core.redis import close_redis
from app.core.settings import cfg
from app.plugins.lifespan import init_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    await init_database()
    yield
    await close_http_client()
    await close_redis()
    await close_db()


def register_middlewares(app: FastAPI) -> None:
    """注册全局中间件"""
    app.middleware("http")(middlewares.auth.middleware)
    app.middleware("http")(middlewares.trace.middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器"""
    app.add_exception_handler(
        base.ProblemError,
        cast(ExceptionHandler, exc_handlers.problem_error_handler),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, exc_handlers.validation_error_handler),
    )
    app.add_exception_handler(
        HTTPException,
        cast(ExceptionHandler, exc_handlers.http_exception_handler),
    )
    app.add_exception_handler(
        Exception,
        cast(ExceptionHandler, exc_handlers.unhandled_exception_handler),
    )


def register_routers(app: FastAPI) -> None:
    """注册路由"""
    app.include_router(routers.chat.router, prefix="/api/chat")
    app.include_router(routers.attachment.router, prefix="/api/chat/attachment")
    app.include_router(routers.admin.router, prefix="/api")
    routers.frontend.register_frontend(app)


def create_app() -> FastAPI:
    """创建并组装 FastAPI 应用"""
    app = FastAPI(lifespan=lifespan)
    register_middlewares(app)
    register_exception_handlers(app)
    register_routers(app)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=cfg.port)
