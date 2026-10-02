"""公开的目录与资产版本读取用例，调用方不接触仓储或数据库会话。"""

from app.metadata.contracts import (
    AssetVersions,
    ColumnKey,
    QueryCatalogColumn,
    QueryCatalogSnapshot,
)
from app.metadata.repositories.postgres import MetaPGRepo
from app.shared.clients.postgres_client_manager import PostgresClientManager


class MetadataReader:
    """为查询提供单次读取的目录和资产版本快照。"""

    def __init__(self, postgres: PostgresClientManager):
        """绑定读取目录与资产版本所需的数据库会话管理器。"""
        self._postgres = postgres

    async def query_catalog(self) -> QueryCatalogSnapshot:
        """在目录会话内转换普通值，返回后不携带 ORM 实体。"""
        async with self._postgres.session() as session:
            repo = MetaPGRepo(session)
            tables = await repo.list_table_infos()
            columns = await repo.list_column_infos()
            return QueryCatalogSnapshot(
                table_names=tuple(table.name for table in tables),
                columns=tuple(
                    QueryCatalogColumn(column.t_name, column.name, column.type)
                    for column in columns
                ),
            )

    async def asset_versions(
        self, table_names: set[str], column_keys: set[ColumnKey]
    ) -> AssetVersions:
        """只返回调用方请求且仍存在的资产版本。"""
        if not table_names and not column_keys:
            return AssetVersions(tables={}, columns={})
        async with self._postgres.session() as session:
            return await MetaPGRepo(session).asset_versions(table_names, column_keys)
