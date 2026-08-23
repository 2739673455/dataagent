"""元数据变更触发查询经验失效测试"""

import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from app.metadata.models import TableInfo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.catalog import MetaCatalogService
from app.metadata.services.contracts import MetadataAssetInvalidator
from app.metadata.services.import_service import ImportMode, MetaImportService
from app.metadata.services.index import MetaIndexService
from app.shared.config.meta_config import MetaConfig, TableConfig
from tests.identity.test_auth_service import AsyncSessionStub


def build_catalog_service(
    meta_repo: MagicMock,
    source_repo: MagicMock,
    invalidator: MagicMock,
) -> MetaCatalogService:
    meta_repo.session = AsyncSessionStub()
    return MetaCatalogService(
        meta_repo=cast(MetaPGRepo, meta_repo),
        source_repo=cast(SourceDorisRepo, source_repo),
        meta_index_service=cast(MetaIndexService, MagicMock()),
        asset_invalidator=cast(MetadataAssetInvalidator, invalidator),
    )


class MetaQueryExperienceInvalidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_invalidates_only_when_table_metadata_changes(self) -> None:
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.upsert_table_info = AsyncMock(side_effect=[True, False])
        source_repo = MagicMock(spec=SourceDorisRepo)
        source_repo.table_exists = AsyncMock(return_value=True)
        source_repo.get_primary_key_columns = AsyncMock(return_value=["id"])
        invalidator = MagicMock(spec=MetadataAssetInvalidator)
        invalidator.invalidate_assets = AsyncMock()
        service = build_catalog_service(meta_repo, source_repo, invalidator)

        await service.upsert_table_info(
            t_name="orders",
            role="fact",
            description="订单事实表",
        )
        await service.upsert_table_info(
            t_name="orders",
            role="fact",
            description="订单事实表",
        )

        invalidator.invalidate_assets.assert_awaited_once_with(
            table_names={"orders"},
            column_keys=set(),
        )

    async def test_batch_import_invalidates_updated_tables(self) -> None:
        existing = TableInfo(
            name="orders",
            role="fact",
            primary_key_columns=["id"],
            description="旧描述",
            meta_version=1,
        )
        imported = TableInfo(
            name="orders",
            role="fact",
            primary_key_columns=["id"],
            description="新描述",
        )
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.list_table_infos = AsyncMock(return_value=[existing])
        meta_repo.list_column_infos = AsyncMock(return_value=[])
        meta_repo.list_metric_infos = AsyncMock(return_value=[])
        meta_repo.upsert_table_info = AsyncMock()
        meta_repo.upsert_column_infos = AsyncMock()
        meta_repo.session = AsyncSessionStub()
        source_repo = MagicMock(spec=SourceDorisRepo)
        meta_index_service = MagicMock(spec=MetaIndexService)
        invalidator = MagicMock(spec=MetadataAssetInvalidator)
        invalidator.invalidate_assets = AsyncMock()
        service = MetaImportService(
            meta_repo=cast(MetaPGRepo, meta_repo),
            source_repo=cast(SourceDorisRepo, source_repo),
            meta_index_service=cast(MetaIndexService, meta_index_service),
            asset_invalidator=cast(MetadataAssetInvalidator, invalidator),
        )
        config = MetaConfig(
            tables=[
                TableConfig(
                    name="orders",
                    role="fact",
                    description="新描述",
                    columns=[],
                )
            ]
        )

        with patch.object(
            service,
            "_build_metadata",
            AsyncMock(return_value=([imported], [], [])),
        ):
            result = await service.import_metadata(
                meta_config=config,
                mode=ImportMode.MERGE,
                dry_run=False,
            )

        self.assertEqual(result.tables.updated, ["orders"])
        invalidator.invalidate_assets.assert_awaited_once_with(
            table_names={"orders"},
            column_keys=set(),
        )


if __name__ == "__main__":
    unittest.main()
