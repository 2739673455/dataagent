from contextlib import asynccontextmanager
from typing import cast

from app.core import cfg, close_db, get_db_session_context
from app.core.exceptions import base, exc_handlers
from app.core.logging import setup_logger
from app.core.middlewares import trace
from app.modules import admin, frontend, health, oauth, user
from app.modules.admin import AdminService, permission_repo, relation_repo, role_repo
from app.modules.oauth import OAuthService, auth_code_repo
from app.modules.shared import session_repo, token_repo, user_repo
from app.modules.user import UserService, email_code_repo
from app.plugins.lifespan import create_admin_user, init_database
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ExceptionHandler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化日志
    setup_logger()
    # 启动时自动补齐数据库结构
    init_database()
    # 启动时确保管理员用户和管理员权限存在
    async with get_db_session_context(
        cfg.db.selected,
        cfg.db.driver,
    ) as db_session:
        await create_admin_user(db_session)

    yield

    # 关闭数据库资源
    await close_db()


def register_middlewares(app: FastAPI) -> None:
    """注册中间件"""
    app.middleware("http")(trace.middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors.origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册异常处理器"""
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
    db_session_context_factory = lambda: get_db_session_context(
        cfg.db.selected, cfg.db.driver
    )

    oauth_service = OAuthService(
        db_session_context_factory=db_session_context_factory,
        auth_config=cfg.auth,
        auth_code_repo=auth_code_repo,
        session_repo=session_repo,
        token_repo=token_repo,
        user_repo=user_repo,
    )
    user_service = UserService(
        db_session_context_factory=db_session_context_factory,
        auth_config=cfg.auth,
        email_config=cfg.email,
        user_repo=user_repo,
        email_code_repo=email_code_repo,
        session_repo=session_repo,
        token_repo=token_repo,
    )
    admin_service = AdminService(
        db_session_context_factory=db_session_context_factory,
        user_repo=user_repo,
        session_repo=session_repo,
        token_repo=token_repo,
        role_repo=role_repo,
        relation_repo=relation_repo,
        permission_repo=permission_repo,
    )

    app.include_router(health.router, prefix="")
    app.include_router(
        oauth.create_router(cfg.app, cfg.cookie, oauth_service),
        prefix="/api",
        tags=["认证"],
    )
    app.include_router(
        user.create_router(cfg.cookie, user_service),
        prefix="/api",
        tags=["用户"],
    )
    app.include_router(
        admin.create_router(admin_service),
        prefix="/api/admin",
        tags=["权限管理"],
    )
    frontend.register_frontend(app)


def create_app() -> FastAPI:
    """创建应用"""
    app = FastAPI(lifespan=lifespan)
    register_middlewares(app)
    register_exception_handlers(app)
    register_routers(app)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=cfg.app.host,
        port=cfg.app.port,
    )
