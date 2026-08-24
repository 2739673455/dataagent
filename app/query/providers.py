"""查询经验服务组装"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.contracts import QueryExperienceIndexScheduler
from app.query.services.experience import QueryExperienceService
from app.query.task_scheduler import query_experience_index_scheduler
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.config.app_config import cfg


def build_query_experience_service(
    session: AsyncSession,
    *,
    index_scheduler: QueryExperienceIndexScheduler = query_experience_index_scheduler,
) -> QueryExperienceService:
    """创建查询经验记录、检索与索引维护服务"""
    return QueryExperienceService(
        repo=QueryExperiencePGRepo(session=session),
        index_repo=QueryExperienceESRepo(client=es_client_manager.get_client()),
        embedding_client=embedding_client_manager.get_client(),
        index_scheduler=index_scheduler,
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )
