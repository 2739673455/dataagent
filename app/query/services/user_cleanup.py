"""用户查询历史清理服务"""

from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.shared.clients.es_client_manager import ESClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager


class QueryHistoryCleanupService:
    """删除用户查询记录及其 Elasticsearch 经验索引"""

    def __init__(
        self,
        postgres: PostgresClientManager,
        es: ESClientManager,
    ) -> None:
        """绑定查询历史使用的 PostgreSQL 和 Elasticsearch"""
        self._postgres = postgres
        self._es = es

    async def delete_user_query_history(self, user_id: int) -> None:
        """删除用户全部查询记录和查询经验"""
        async with self._postgres.session() as session:
            repo = QueryExperiencePGRepo(session)
            async with session.begin():
                experience_ids = await repo.list_ids_by_user(user_id)

        await QueryExperienceESRepo(self._es.get_client()).delete_many(experience_ids)

        async with self._postgres.session() as session:
            repo = QueryExperiencePGRepo(session)
            async with session.begin():
                await repo.delete_by_user(user_id)
