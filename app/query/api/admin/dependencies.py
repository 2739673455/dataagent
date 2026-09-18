"""查询经验管理接口依赖。"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import WebResourcesDep
from app.query.repositories.execution_postgres import QueryExecutionPGRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.experience_management import QueryExperienceManagementService
from app.query.task_scheduler import query_experience_index_scheduler


async def _get_meta_session(resources: WebResourcesDep) -> AsyncGenerator[AsyncSession]:
    """为查询经验管理创建独立请求会话。"""
    async with resources.meta.session() as session:
        yield session


MetaSessionDep = Annotated[AsyncSession, Depends(_get_meta_session)]


def _get_query_experience_management_service(
    session: MetaSessionDep,
) -> QueryExperienceManagementService:
    """创建请求级查询经验管理服务。"""
    return QueryExperienceManagementService(
        QueryExperiencePGRepo(session),
        QueryExecutionPGRepo(session),
        query_experience_index_scheduler,
    )


QueryExperienceManagementServiceDep = Annotated[
    QueryExperienceManagementService,
    Depends(_get_query_experience_management_service),
]
