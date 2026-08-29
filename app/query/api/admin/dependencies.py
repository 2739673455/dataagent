"""查询经验管理接口依赖"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.experience_management import QueryExperienceManagementService
from app.query.task_scheduler import query_experience_index_scheduler
from app.shared.clients.postgres_client_manager import meta_postgres_client_manager

MetaSessionDep = Annotated[
    AsyncSession,
    Depends(meta_postgres_client_manager.get_session),
]


def _get_query_experience_management_service(
    session: MetaSessionDep,
) -> QueryExperienceManagementService:
    """创建请求级查询经验管理服务。"""
    return QueryExperienceManagementService(
        QueryExperiencePGRepo(session),
        query_experience_index_scheduler,
    )


QueryExperienceManagementServiceDep = Annotated[
    QueryExperienceManagementService,
    Depends(_get_query_experience_management_service),
]
