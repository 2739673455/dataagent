"""查询执行历史与经验 PostgreSQL 数据访问"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Text, cast, delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.metadata.models.catalog import (
    ColumnInfo,
    TableInfo,
)
from app.query.models.execution import QueryExecution
from app.query.models.experience import (
    QueryExperience,
    QueryExperienceAsset,
    QueryExperienceOverview,
)


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
                status="active",
                disabled_reason=None,
                disabled_by_user_id=None,
                disabled_at=None,
                deletion_requested_by_user_id=None,
                deletion_requested_at=None,
                revision=1,
                indexed_revision=0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["role_name", "fingerprint"])
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
            experience_updated = existing.refresh_from_success(
                purpose=experience.purposes[0],
                authorization_epoch=experience.authorization_epoch,
                sql_template=experience.sql_template,
            )
        else:
            experience_id = inserted_id
            experience_updated = True

        if experience_updated:
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

    async def record_execution(self, execution: QueryExecution) -> None:
        """写入不参与经验聚合的成功执行"""
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

    async def disable_for_changed_resources(
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
                        QueryExperience.status == "active",
                        QueryExperienceAsset.resource_key.in_(resource_keys),
                    )
                    .distinct()
                )
            ).all()
        )
        return await self.disable_for_metadata_change(experience_ids)

    async def disable_for_metadata_change(
        self,
        experience_ids: set[UUID] | list[UUID],
    ) -> dict[UUID, int]:
        """因元数据变化禁用指定经验并返回新版本"""
        if not experience_ids:
            return {}
        now = datetime.now(UTC)
        result = await self._session.execute(
            update(QueryExperience)
            .where(
                QueryExperience.id.in_(experience_ids),
                QueryExperience.status == "active",
            )
            .values(
                status="disabled",
                disabled_reason="metadata_changed",
                disabled_by_user_id=None,
                disabled_at=now,
                revision=QueryExperience.revision + 1,
                updated_at=now,
            )
            .returning(QueryExperience.id, QueryExperience.revision)
        )
        await self._session.flush()
        return dict(result.tuples())

    async def list_overviews(
        self,
        *,
        limit: int,
        offset: int,
        role_name: str | None,
        status: str | None,
        query: str | None,
    ) -> tuple[list[QueryExperienceOverview], int]:
        """按筛选条件分页读取查询经验概览。"""
        filters = []
        if role_name is not None:
            filters.append(QueryExperience.role_name == role_name)
        if status is not None:
            filters.append(QueryExperience.status == status)
        if query is not None:
            pattern = f"%{query}%"
            filters.append(
                or_(
                    QueryExperience.sql_template.ilike(pattern),
                    QueryExperience.fingerprint.ilike(pattern),
                    cast(QueryExperience.purposes, Text).ilike(pattern),
                )
            )

        asset_count = (
            select(func.count(QueryExperienceAsset.id))
            .where(QueryExperienceAsset.experience_id == QueryExperience.id)
            .correlate(QueryExperience)
            .scalar_subquery()
        )
        execution_count = (
            select(func.count(QueryExecution.id))
            .where(QueryExecution.experience_id == QueryExperience.id)
            .correlate(QueryExperience)
            .scalar_subquery()
        )
        last_executed_at = (
            select(func.max(QueryExecution.created_at))
            .where(QueryExecution.experience_id == QueryExperience.id)
            .correlate(QueryExperience)
            .scalar_subquery()
        )
        total = await self._session.scalar(
            select(func.count()).select_from(QueryExperience).where(*filters)
        )
        result = await self._session.execute(
            select(
                QueryExperience,
                asset_count.label("asset_count"),
                execution_count.label("execution_count"),
                last_executed_at.label("last_executed_at"),
            )
            .where(*filters)
            .order_by(QueryExperience.updated_at.desc(), QueryExperience.id)
            .limit(limit)
            .offset(offset)
        )
        rows = [
            QueryExperienceOverview(
                experience=experience,
                asset_count=asset_total,
                execution_count=execution_total,
                last_executed_at=last_execution,
            )
            for experience, asset_total, execution_total, last_execution in result.all()
        ]
        return rows, total or 0

    async def get_overview(
        self,
        experience_id: UUID,
    ) -> QueryExperienceOverview | None:
        """读取一条查询经验及其聚合统计。"""
        experience = await self.get(experience_id)
        if experience is None:
            return None
        execution_count, last_executed_at = (
            await self._session.execute(
                select(
                    func.count(QueryExecution.id),
                    func.max(QueryExecution.created_at),
                ).where(QueryExecution.experience_id == experience_id)
            )
        ).one()
        return QueryExperienceOverview(
            experience=experience,
            asset_count=len(experience.assets),
            execution_count=execution_count,
            last_executed_at=last_executed_at,
        )

    async def list_source_executions(
        self,
        experience_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[QueryExecution], int]:
        """分页读取一条经验的来源执行记录。"""
        filters = (
            QueryExecution.experience_id == experience_id,
            QueryExecution.status == "succeeded",
        )
        total = await self._session.scalar(
            select(func.count()).select_from(QueryExecution).where(*filters)
        )
        executions = list(
            (
                await self._session.scalars(
                    select(QueryExecution)
                    .where(*filters)
                    .order_by(
                        QueryExecution.created_at.desc(), QueryExecution.id.desc()
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        return executions, total or 0

    async def disable_manually(
        self,
        experience_id: UUID,
        admin_user_id: int,
    ) -> tuple[QueryExperience | None, bool]:
        """按行锁将查询经验标记为管理员禁用。"""
        experience = await self._session.scalar(
            select(QueryExperience)
            .options(selectinload(QueryExperience.assets))
            .where(QueryExperience.id == experience_id)
            .with_for_update()
        )
        if experience is None or experience.status == "deleting":
            return experience, False
        if experience.status == "disabled" and experience.disabled_reason == "admin":
            return experience, False
        now = datetime.now(UTC)
        experience.status = "disabled"
        experience.disabled_reason = "admin"
        experience.disabled_by_user_id = admin_user_id
        experience.disabled_at = now
        experience.deletion_requested_by_user_id = None
        experience.deletion_requested_at = None
        experience.revision += 1
        experience.updated_at = now
        await self._session.flush()
        return experience, True

    async def request_deletion(
        self,
        experience_id: UUID,
        admin_user_id: int,
    ) -> tuple[QueryExperience | None, bool]:
        """按行锁将查询经验标记为删除中。"""
        experience = await self._session.scalar(
            select(QueryExperience)
            .options(selectinload(QueryExperience.assets))
            .where(QueryExperience.id == experience_id)
            .with_for_update()
        )
        if experience is None:
            return None, False
        if experience.status == "deleting":
            return experience, False
        now = datetime.now(UTC)
        experience.status = "deleting"
        experience.disabled_reason = None
        experience.disabled_by_user_id = None
        experience.disabled_at = None
        experience.deletion_requested_by_user_id = admin_user_id
        experience.deletion_requested_at = now
        experience.revision += 1
        experience.updated_at = now
        await self._session.flush()
        return experience, True

    async def finalize_deletion(self, experience_id: UUID, revision: int) -> bool:
        """删除已完成当前索引清理的查询经验。"""
        deleted_id = await self._session.scalar(
            delete(QueryExperience)
            .where(
                QueryExperience.id == experience_id,
                QueryExperience.revision == revision,
                QueryExperience.status == "deleting",
            )
            .returning(QueryExperience.id)
        )
        await self._session.flush()
        return deleted_id is not None

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
        return dict(result.tuples())

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
