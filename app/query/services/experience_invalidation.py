"""元数据变化触发的查询经验失效"""

from uuid import UUID

from app.identity.models.doris import asset_resource_key
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.contracts import QueryExperienceIndexScheduler


class QueryExperienceInvalidationService:
    """禁用引用已变化元数据的查询经验并安排索引同步"""

    def __init__(
        self,
        repo: QueryExperiencePGRepo,
        index_scheduler: QueryExperienceIndexScheduler,
        *,
        data_source: str,
        database_name: str,
    ) -> None:
        self._repo = repo
        self._index_scheduler = index_scheduler
        self._data_source = data_source
        self._database_name = database_name

    async def invalidate_assets(
        self,
        *,
        table_names: set[str],
        column_keys: set[tuple[str, str]],
    ) -> list[UUID]:
        """禁用引用指定元数据资产的经验并提交其新版本"""
        resource_keys = {
            asset_resource_key(
                self._data_source,
                self._database_name,
                table_name,
            )
            for table_name in table_names
        }
        resource_keys.update(
            asset_resource_key(
                self._data_source,
                self._database_name,
                table_name,
                column_name,
            )
            for table_name, column_name in column_keys
        )
        async with self._repo.session.begin():
            revisions = await self._repo.disable_for_changed_resources(resource_keys)
        for experience_id, revision in revisions.items():
            self._index_scheduler.enqueue(experience_id, revision)
        return list(revisions)
