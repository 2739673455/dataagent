from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.analytics.api.attachment.router import router as attachment_router
from app.analytics.api.chat.router import router as chat_router
from app.identity.api.admin.router import router as admin_router
from app.identity.api.admin.task_router import router as task_router
from app.identity.api.auth.router import router as auth_router
from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.repositories.query_identity import DorisQueryIdentityPGRepo
from app.identity.services.credential import DorisCredentialCipher
from app.metadata.api.meta.router import router as meta_router
from app.providers import agent_manager, sandbox_manager
from app.query.repositories.doris import DorisQueryRepository
from app.shared.clients.doris_client_manager import (
    admin_doris_client_manager,
    query_doris_client_registry,
)
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
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


async def verify_doris_query_identities() -> None:
    """校验数据库中全部启用查询身份的 Doris 权限"""
    cipher = DorisCredentialCipher(
        cfg.doris_credentials.encryption_key.get_secret_value()
    )
    async with auth_postgres_client_manager.session() as session:
        identities = await DorisQueryIdentityPGRepo(session).list_active()
    await DorisRoleRepository(admin_doris_client_manager).verify_configured_roles(
        tuple(identity.role_name for identity in identities)
    )
    for identity in identities:
        try:
            manager = await query_doris_client_registry.get_or_create(
                identity.role_name,
                identity.query_user,
                cipher.decrypt(identity.encrypted_password),
            )
            await DorisQueryRepository(manager).verify_readonly_access(
                identity.workload_group,
                cfg.doris.database,
                identity.role_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Doris 角色 '{identity.role_name}' 未完成目标库表授权或校验未通过: {exc}"
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """初始化并释放应用进程持有的共享资源"""
    try:
        # FastAPI 应用启动前执行
        logger.info("开始初始化应用资源")
        embedding_client_manager.init()
        es_client_manager.init()
        await langgraph_postgres_manager.init()
        await sandbox_manager.init()
        await agent_manager.init()
        auth_postgres_client_manager.init()
        await auth_postgres_client_manager.init_tables()
        meta_postgres_client_manager.init()
        await meta_postgres_client_manager.init_tables()
        admin_doris_client_manager.init()
        await verify_doris_query_identities()
        logger.info("应用资源初始化完成")

        yield
    finally:
        # FastAPI 应用结束前执行
        logger.info("开始释放应用资源")
        await agent_manager.close()
        await sandbox_manager.close()
        await langgraph_postgres_manager.close()
        await embedding_client_manager.close()
        await es_client_manager.close()
        await meta_postgres_client_manager.close()
        await auth_postgres_client_manager.close()
        await admin_doris_client_manager.close()
        await query_doris_client_registry.close()
        logger.info("应用资源释放完成")


def register_routes(app: FastAPI) -> None:
    """注册接口"""
    app.include_router(auth_router, prefix="/api/v1/auth")
    app.include_router(admin_router, prefix="/api/v1/admin")
    app.include_router(chat_router, prefix="/api/v1/chat")
    app.include_router(
        attachment_router,
        prefix="/api/v1/chat/attachment",
    )
    app.include_router(meta_router, prefix="/api/v1/meta")
    app.include_router(task_router, prefix="/api/v1/tasks")


def register_middlewares(app: FastAPI) -> None:
    """注册中间件"""
    app.middleware("http")(trace.middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def create_app() -> FastAPI:
    """创建并组装 FastAPI 应用"""
    setup_logger()
    app = FastAPI(lifespan=lifespan, responses=_ERROR_RESPONSES)
    register_middlewares(app)
    register_exception_handlers(app)
    register_routes(app)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port)
