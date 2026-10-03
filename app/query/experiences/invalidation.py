"""元数据变化触发的查询经验失效。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.contracts.assets import asset_resource_key

if TYPE_CHECKING:
    from app.query.experiences.scheduler import CeleryQueryExperienceIndexScheduler


class QueryExperienceInvalidationService:
    """禁用引用已变化元数据的查询经验并安排索引同步。"""

    def __init__(
        self,
        postgres: PostgresClientManager,
        index_scheduler: CeleryQueryExperienceIndexScheduler,
        *,
        data_source: str,
        database_name: str,
    ) -> None:
        """绑定查询数据库、索引调度器和元数据资产命名空间。"""
        self._postgres = postgres
        self._index_scheduler = index_scheduler
        self._data_source = data_source
        self._database_name = database_name

    async def invalidate_assets(
        self,
        *,
        table_names: set[str],
        column_keys: set[tuple[str, str]],
    ) -> list[UUID]:
        """禁用引用指定元数据资产的经验并提交其新版本。"""
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
        async with self._postgres.session() as session, session.begin():
            revisions = await QueryExperiencePGRepo(
                session
            ).disable_for_changed_resources(resource_keys)
        for experience_id, revision in revisions.items():
            self._index_scheduler.enqueue(experience_id, revision)
        return list(revisions)
