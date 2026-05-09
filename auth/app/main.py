from contextlib import asynccontextmanager
from functools import partial
from typing import cast

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ExceptionHandler

from app.application.admin.use_cases import create_admin_use_cases
from app.application.oauth.use_cases import create_oauth_use_cases
from app.application.shared.session_creator import SessionCreator
from app.application.shared.token_issuer import TokenIssuer
from app.application.user.use_cases import create_user_use_cases
from app.core import cfg, close_db, get_db_session_context
from app.core.exceptions import base, exc_handlers
from app.core.log_setup import setup_logger
from app.core.middlewares import trace
from app.infrastructure.email_sender import SmtpEmailSender
from app.infrastructure.repos.auth_code_repo import AuthCodeRepo
from app.infrastructure.repos.email_code_repo import EmailCodeRepo
from app.infrastructure.repos.permission_repo import PermissionRepo
from app.infrastructure.repos.relation_repo import RelationRepo
from app.infrastructure.repos.role_repo import RoleRepo
from app.infrastructure.repos.session_repo import SessionRepo
from app.infrastructure.repos.token_repo import TokenRepo
from app.infrastructure.repos.user_repo import UserRepo
from app.infrastructure.security import (
    PasswordHasherImpl,
    PkceServiceImpl,
    TokenFactoryImpl,
)
from app.plugins.lifespan import create_admin_user, init_database
from app.presentation import frontend
from app.presentation.admin.router import create_router as create_admin_router
from app.presentation.oauth.router import create_router as create_oauth_router
from app.presentation.user.router import create_router as create_user_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    init_database()
    async with get_db_session_context(cfg.db.selected, cfg.db.driver) as db_session:
        await create_admin_user(db_session, password_hasher)
    yield
    await close_db()


def register_middlewares(app: FastAPI) -> None:
    """注册全局中间件"""
    app.middleware("http")(trace.middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors.origins,
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
    """组装依赖并注册路由"""
    # 预填函数参数
    db_factory = partial(get_db_session_context, cfg.db.selected, cfg.db.driver)

    # --- 基础设施实例 ---
    token_factory = TokenFactoryImpl()
    pkce = PkceServiceImpl()

    user_repo = UserRepo(password_hasher)
    token_repo = TokenRepo()
    session_repo = SessionRepo()
    auth_code_repo = AuthCodeRepo()
    email_code_repo = EmailCodeRepo()
    email_sender = SmtpEmailSender(cfg.email)

    # --- 共享领域服务 ---
    session_creator = SessionCreator(session_repo, cfg.auth, token_factory)
    token_issuer = TokenIssuer(token_repo, user_repo, cfg.auth, token_factory)

    # --- OAuth 用例 ---
    oauth_use_cases = create_oauth_use_cases(
        db_factory=db_factory,
        auth_config=cfg.auth,
        auth_code_repo=auth_code_repo,
        session_repo=session_repo,
        token_repo=token_repo,
        user_repo=user_repo,
        password_hasher=password_hasher,
        token_factory=token_factory,
        pkce=pkce,
        token_issuer=token_issuer,
        session_creator=session_creator,
    )

    # --- User 用例 ---
    user_use_cases = create_user_use_cases(
        db_factory=db_factory,
        auth_config=cfg.auth,
        email_config=cfg.email,
        user_repo=user_repo,
        email_code_repo=email_code_repo,
        session_repo=session_repo,
        token_repo=token_repo,
        token_factory=token_factory,
        email_sender=email_sender,
        session_creator=session_creator,
    )

    # --- Admin 用例 ---
    admin_use_cases = create_admin_use_cases(
        db_factory=db_factory,
        user_repo=user_repo,
        session_repo=session_repo,
        token_repo=token_repo,
        role_repo=RoleRepo(),
        permission_repo=PermissionRepo(),
        relation_repo=RelationRepo(),
    )

    # --- 注册路由 ---
    app.include_router(
        create_oauth_router(cfg.app, cfg.cookie, oauth_use_cases, token_repo),
        prefix="/api",
        tags=["认证"],
    )
    app.include_router(
        create_user_router(cfg.cookie, user_use_cases, token_repo),
        prefix="/api",
        tags=["用户"],
    )
    app.include_router(
        create_admin_router(admin_use_cases, token_repo),
        prefix="/api/admin",
        tags=["权限管理"],
    )
    frontend.register_frontend(app)


def create_app() -> FastAPI:
    """创建并组装 FastAPI 应用"""
    app = FastAPI(lifespan=lifespan)
    register_middlewares(app)
    register_exception_handlers(app)
    register_routers(app)
    return app


# 模块级基础设施 — lifespan 和 register_routers 共用
password_hasher = PasswordHasherImpl()

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=cfg.app.host, port=cfg.app.port)
