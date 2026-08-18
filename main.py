from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.manager import agent_manager
from app.clients.docker_sandbox_manager import docker_sandbox_manager
from app.clients.doris_client_manager import (
    query_doris_client_manager,
    source_doris_client_manager,
)
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.clients.postgres_client_manager import meta_postgres_client_manager
from app.conf.app_config import cfg
from app.core.middlewares import trace
from app.errors.exc_handlers import register_exception_handlers
from app.repositories.doris_query_repo import DorisQueryRepository
from app.routes import api


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        # FastAPI 应用启动前执行
        embedding_client_manager.init()
        es_client_manager.init()
        await langgraph_postgres_manager.init()
        await docker_sandbox_manager.init()
        await agent_manager.init()
        meta_postgres_client_manager.init()
        await meta_postgres_client_manager.init_tables()
        source_doris_client_manager.init()
        query_doris_client_manager.init()
        await DorisQueryRepository(query_doris_client_manager).verify_readonly_access(
            cfg.query.workload_group,
            cfg.doris_query.database,
        )

        yield
    finally:
        # FastAPI 应用结束前执行
        await agent_manager.close()
        await docker_sandbox_manager.close()
        await langgraph_postgres_manager.close()
        await embedding_client_manager.close()
        await es_client_manager.close()
        await meta_postgres_client_manager.close()
        await source_doris_client_manager.close()
        await query_doris_client_manager.close()


def register_routes(app: FastAPI) -> None:
    """注册接口"""
    app.include_router(api.v1.auth.router, prefix="/api/v1/auth")
    app.include_router(api.v1.admin.router, prefix="/api/v1/admin")
    app.include_router(api.v1.chat.router, prefix="/api/v1/chat")
    app.include_router(
        api.v1.attachment.router,
        prefix="/api/v1/chat/attachment",
    )
    app.include_router(api.v1.meta.router, prefix="/api/v1/meta")


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
    app = FastAPI(lifespan=lifespan)
    register_middlewares(app)
    register_exception_handlers(app)
    register_routes(app)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=cfg.port)
