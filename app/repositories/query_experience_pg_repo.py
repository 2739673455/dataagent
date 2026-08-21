"""查询执行历史与经验 PostgreSQL 数据访问"""

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.meta import (
    ColumnInfo,
    QueryExecution,
    QueryExperience,
    QueryExperienceAsset,
    TableInfo,
)


class QueryExperiencePGRepo:
    """持久化用户私有查询执行和聚合经验"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
                owner_user_id=experience.owner_user_id,
                role_name=experience.role_name,
                fingerprint=experience.fingerprint,
                dialect=experience.dialect,
                purposes=experience.purposes,
                representative_sql=experience.representative_sql,
                sql_template=experience.sql_template,
                quality="candidate",
                success_count=1,
                adopted_count=0,
                revision=1,
                indexed_revision=0,
                first_used_at=now,
                last_used_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["owner_user_id", "role_name", "fingerprint"]
            )
            .returning(QueryExperience.id)
        )
        inserted_id = await self._session.scalar(statement)
        if inserted_id is None:
            existing = await self._session.scalar(
                select(QueryExperience)
                .where(
                    QueryExperience.owner_user_id == experience.owner_user_id,
                    QueryExperience.role_name == experience.role_name,
                    QueryExperience.fingerprint == experience.fingerprint,
                )
                .with_for_update()
            )
            if existing is None:
                raise RuntimeError("query experience upsert did not return a row")
            experience_id = existing.id
            existing.refresh_from_success(
                purpose=experience.purposes[0],
                representative_sql=experience.representative_sql,
                sql_template=experience.sql_template,
                used_at=now,
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
        await self._session.commit()
        stored = await self.get(experience_id)
        if stored is None:
            raise RuntimeError("recorded query experience is unavailable")
        return stored

    async def record_failure(self, execution: QueryExecution) -> None:
        """写入拒绝或失败的 SQL 尝试"""
        self._session.add(execution)
        await self._session.commit()

    async def get(self, experience_id: UUID) -> QueryExperience | None:
        """读取一条经验及其资产"""
        return await self._session.scalar(
            select(QueryExperience)
            .options(selectinload(QueryExperience.assets))
            .where(QueryExperience.id == experience_id)
        )

    async def list_ids_by_user(self, user_id: int) -> list[UUID]:
        """列出用户全部查询经验主键"""
        result = await self._session.scalars(
            select(QueryExperience.id).where(
                QueryExperience.owner_user_id == user_id
            )
        )
        return list(result)

    async def delete_by_user(self, user_id: int) -> None:
        """删除用户全部查询执行和查询经验"""
        await self._session.execute(
            delete(QueryExecution).where(QueryExecution.user_id == user_id)
        )
        await self._session.execute(
            delete(QueryExperience).where(
                QueryExperience.owner_user_id == user_id
            )
        )
        await self._session.flush()

    async def get_many(
        self,
        user_id: int,
        experience_ids: list[UUID],
        *,
        role_name: str | None,
    ) -> list[QueryExperience]:
        """在用户和可选角色范围内按 ID 批量读取经验"""
        if not experience_ids:
            return []
        conditions = [
            QueryExperience.owner_user_id == user_id,
            QueryExperience.id.in_(experience_ids),
        ]
        if role_name is not None:
            conditions.append(QueryExperience.role_name == role_name)
        result = await self._session.scalars(
            select(QueryExperience)
            .options(selectinload(QueryExperience.assets))
            .where(*conditions)
        )
        by_id = {item.id: item for item in result.unique().all()}
        return [by_id[item_id] for item_id in experience_ids if item_id in by_id]

    async def find_by_assets(
        self,
        user_id: int,
        role_name: str,
        resource_keys: set[str],
        *,
        limit: int,
    ) -> list[QueryExperience]:
        """按当前召回资产查找用户经验"""
        if not resource_keys:
            return []
        result = await self._session.scalars(
            select(QueryExperience)
            .join(QueryExperienceAsset)
            .options(selectinload(QueryExperience.assets))
            .where(
                QueryExperience.owner_user_id == user_id,
                QueryExperience.role_name == role_name,
                QueryExperience.quality != "disabled",
                QueryExperienceAsset.resource_key.in_(resource_keys),
            )
            .distinct()
            .order_by(QueryExperience.last_used_at.desc())
            .limit(limit)
        )
        return list(result.unique().all())

    async def list_recent(
        self,
        user_id: int,
        role_name: str,
        *,
        limit: int,
    ) -> list[QueryExperience]:
        """列出用户最近的可用经验"""
        result = await self._session.scalars(
            select(QueryExperience)
            .options(selectinload(QueryExperience.assets))
            .where(
                QueryExperience.owner_user_id == user_id,
                QueryExperience.role_name == role_name,
                QueryExperience.quality != "disabled",
            )
            .order_by(QueryExperience.last_used_at.desc())
            .limit(limit)
        )
        return list(result.unique().all())

    async def promote_by_artifacts(
        self,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        session_id: str,
        artifact_paths: set[str],
    ) -> list[QueryExperience]:
        """提升被 Explorer 最终结果采用的查询经验"""
        if not artifact_paths:
            return []
        now = datetime.now(UTC)
        executions = list(
            (
                await self._session.scalars(
                    select(QueryExecution)
                    .join(
                        QueryExperience,
                        QueryExperience.id == QueryExecution.experience_id,
                    )
                    .where(
                        QueryExecution.user_id == user_id,
                        QueryExecution.conversation_id == conversation_id,
                        QueryExecution.analysis_id == analysis_id,
                        QueryExecution.session_id == session_id,
                        QueryExecution.status == "succeeded",
                        QueryExecution.adopted_at.is_(None),
                        QueryExecution.artifact_path.in_(artifact_paths),
                        QueryExperience.owner_user_id == user_id,
                        QueryExperience.quality != "disabled",
                    )
                    .with_for_update()
                )
            ).all()
        )
        counts = Counter(
            execution.experience_id
            for execution in executions
            if execution.experience_id is not None
        )
        if not counts:
            return []
        for execution in executions:
            execution.adopted_at = now
        for experience_id, count in counts.items():
            await self._session.execute(
                update(QueryExperience)
                .where(QueryExperience.id == experience_id)
                .values(
                    quality="promoted",
                    adopted_count=QueryExperience.adopted_count + count,
                    revision=QueryExperience.revision + 1,
                    last_adopted_at=now,
                    updated_at=now,
                )
            )
        await self._session.commit()
        return await self.get_many(
            user_id,
            list(counts),
            role_name=None,
        )

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
                invalidated_at=now,
                updated_at=now,
            )
            .returning(QueryExperience.id, QueryExperience.revision)
        )
        await self._session.commit()
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
        await self._session.commit()

    async def list_pending_index_deletions(
        self,
        user_id: int,
        role_name: str,
        *,
        limit: int,
    ) -> dict[UUID, int]:
        """列出当前用户尚未完成索引删除的失效经验"""
        result = await self._session.execute(
            select(QueryExperience.id, QueryExperience.revision)
            .where(
                QueryExperience.owner_user_id == user_id,
                QueryExperience.role_name == role_name,
                QueryExperience.quality == "disabled",
                QueryExperience.indexed_revision < QueryExperience.revision,
            )
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
