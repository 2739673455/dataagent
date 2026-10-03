"""元数据目录与资产版本的公开读取用例。"""

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
        """在目录数据库会话内将 ORM 实体转换为目录快照。"""
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
        """读取请求范围内现存资产的版本。"""
        if not table_names and not column_keys:
            return AssetVersions(tables={}, columns={})
        async with self._postgres.session() as session:
            return await MetaPGRepo(session).asset_versions(table_names, column_keys)
