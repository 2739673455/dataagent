"""查询执行历史与经验 PostgreSQL 数据访问"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.metadata.models.catalog import (
    ColumnInfo,
    TableInfo,
)
from app.query.models.execution import QueryExecution
from app.query.models.experience import QueryExperience, QueryExperienceAsset


class QueryExperiencePGRepo:
    """持久化查询执行审计和角色级聚合经验"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求使用的异步数据库会话"""
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """返回当前存储绑定的数据库会话"""
        return self._session

    async def record_success(
        self,
        execution: QueryExecution,
        experience: QueryExperience,
        assets: list[QueryExperienceAsset],
    ) -> QueryExperience:
        """原子写入成功执行并更新相同 SQL 指纹的经验"""
        now = datetime.now(UTC)
        proposed_id = experience.id or uuid4()
        statement = (
            insert(QueryExperience)
            .values(
                id=proposed_id,
                role_name=experience.role_name,
                authorization_epoch=experience.authorization_epoch,
                fingerprint=experience.fingerprint,
                purposes=experience.purposes,
                sql_template=experience.sql_template,
                quality="candidate",
                revision=1,
                indexed_revision=0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["role_name", "fingerprint"]
            )
            .returning(QueryExperience.id)
        )
        inserted_id = await self._session.scalar(statement)
        if inserted_id is None:
            existing = await self._session.scalar(
                select(QueryExperience)
                .where(
                    QueryExperience.role_name == experience.role_name,
                    QueryExperience.fingerprint == experience.fingerprint,
                )
                .with_for_update()
            )
            if existing is None:
                raise RuntimeError("查询经验写入未返回记录行")
            experience_id = existing.id
            existing.refresh_from_success(
                purpose=experience.purposes[0],
                authorization_epoch=experience.authorization_epoch,
                sql_template=experience.sql_template,
            )
        else:
            experience_id = inserted_id

        await self._session.execute(
            delete(QueryExperienceAsset).where(
                QueryExperienceAsset.experience_id == experience_id
            )
        )
        for asset in assets:
            asset.experience_id = experience_id
        self._session.add_all(assets)
        execution.experience_id = experience_id
        self._session.add(execution)
        await self._session.flush()
        stored = await self.get(experience_id)
        if stored is None:
            raise RuntimeError("已记录的查询经验不可用")
        return stored

    async def record_failure(self, execution: QueryExecution) -> None:
        """写入拒绝或失败的 SQL 尝试"""
        self._session.add(execution)
        await self._session.flush()

    async def get(self, experience_id: UUID) -> QueryExperience | None:
        """读取一条经验及其资产"""
        return await self._session.scalar(
            select(QueryExperience)
            .options(selectinload(QueryExperience.assets))
            .where(QueryExperience.id == experience_id)
        )

    async def get_many(
        self,
        experience_ids: list[UUID],
        *,
        role_name: str,
        authorization_epoch: UUID,
    ) -> list[QueryExperience]:
        """在当前角色和授权代次范围内按 ID 批量读取经验。"""
        if not experience_ids:
            return []
        conditions = [
            QueryExperience.id.in_(experience_ids),
            QueryExperience.role_name == role_name,
            QueryExperience.authorization_epoch == authorization_epoch,
        ]
        result = await self._session.scalars(
            select(QueryExperience)
            .options(selectinload(QueryExperience.assets))
            .where(*conditions)
        )
        by_id = {item.id: item for item in result.unique().all()}
        return [by_id[item_id] for item_id in experience_ids if item_id in by_id]

    async def disable_by_resource_keys(
        self,
        resource_keys: set[str],
    ) -> dict[UUID, int]:
        """禁用引用指定元数据资产的全部有效经验"""
        if not resource_keys:
            return {}
        experience_ids = list(
            (
                await self._session.scalars(
                    select(QueryExperience.id)
                    .join(QueryExperienceAsset)
                    .where(
                        QueryExperience.quality != "disabled",
                        QueryExperienceAsset.resource_key.in_(resource_keys),
                    )
                    .distinct()
                )
            ).all()
        )
        return await self.disable(experience_ids)

    async def disable(self, experience_ids: set[UUID] | list[UUID]) -> dict[UUID, int]:
        """禁用指定经验并返回失效后的版本"""
        if not experience_ids:
            return {}
        now = datetime.now(UTC)
        result = await self._session.execute(
            update(QueryExperience)
            .where(
                QueryExperience.id.in_(experience_ids),
                QueryExperience.quality != "disabled",
            )
            .values(
                quality="disabled",
                revision=QueryExperience.revision + 1,
                updated_at=now,
            )
            .returning(QueryExperience.id, QueryExperience.revision)
        )
        await self._session.flush()
        return {experience_id: revision for experience_id, revision in result.tuples()}

    async def mark_indexes_synced(self, revisions: dict[UUID, int]) -> None:
        """记录经验搜索投影已同步到指定版本"""
        for experience_id, revision in revisions.items():
            await self._session.execute(
                update(QueryExperience)
                .where(
                    QueryExperience.id == experience_id,
                    QueryExperience.revision == revision,
                )
                .values(indexed_revision=revision)
            )
        await self._session.flush()

    async def list_pending_index_repairs(
        self,
        *,
        limit: int,
    ) -> dict[UUID, int]:
        """列出全部尚未同步到当前版本的查询经验"""
        result = await self._session.execute(
            select(QueryExperience.id, QueryExperience.revision)
            .where(QueryExperience.indexed_revision < QueryExperience.revision)
            .order_by(QueryExperience.updated_at)
            .limit(limit)
        )
        return {experience_id: revision for experience_id, revision in result.tuples()}

    async def current_asset_versions(
        self,
        experiences: list[QueryExperience],
    ) -> dict[str, int]:
        """批量读取经验资产当前对应的元数据版本"""
        table_names = {
            asset.table_name
            for experience in experiences
            for asset in experience.assets
            if asset.kind == "table"
        }
        column_keys = {
            (asset.table_name, asset.column_name)
            for experience in experiences
            for asset in experience.assets
            if asset.kind == "column" and asset.column_name is not None
        }
        table_versions, column_versions = await self.metadata_versions(
            table_names,
            {
                (table_name, column_name)
                for table_name, column_name in column_keys
                if column_name is not None
            },
        )
        versions: dict[str, int] = {}
        for experience in experiences:
            for asset in experience.assets:
                if asset.kind == "table":
                    version = table_versions.get(asset.table_name)
                else:
                    version = column_versions.get(
                        (asset.table_name, asset.column_name or "")
                    )
                if version is not None:
                    versions[asset.resource_key] = version
        return versions

    async def metadata_versions(
        self,
        table_names: set[str],
        column_keys: set[tuple[str, str]],
    ) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
        """批量读取指定表和字段的当前元数据版本"""
        table_versions: dict[str, int] = {}
        column_versions: dict[tuple[str, str], int] = {}
        if table_names:
            tables = await self._session.scalars(
                select(TableInfo).where(TableInfo.name.in_(table_names))
            )
            for table in tables:
                table_versions[table.name] = table.meta_version
        if column_keys:
            columns = await self._session.scalars(
                select(ColumnInfo).where(
                    tuple_(ColumnInfo.t_name, ColumnInfo.name).in_(column_keys)
                )
            )
            for column in columns:
                column_versions[(column.t_name, column.name)] = column.meta_version
        return table_versions, column_versions
