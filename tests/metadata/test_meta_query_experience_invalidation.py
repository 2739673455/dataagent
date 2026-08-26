"""元数据变更触发查询经验失效测试"""

import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from app.metadata.models.catalog import ColumnInfo, MetricInfo, TableInfo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.catalog import MetaCatalogService
from app.metadata.services.contracts import (
    MetadataAssetInvalidator,
    MetadataSemanticIndexScheduler,
)
from app.metadata.services.import_service import ImportMode, MetaImportService
from app.metadata.services.index import MetaIndexService
from app.shared.config.meta_config import MetaConfig, TableConfig
from app.shared.tasks.submission import TaskSubmission
from tests.identity.test_auth_service import AsyncSessionStub


def build_catalog_service(
    meta_repo: MagicMock,
    source_repo: MagicMock,
    invalidator: MagicMock,
    scheduler: MagicMock | None = None,
) -> MetaCatalogService:
    meta_repo.session = AsyncSessionStub()
    return MetaCatalogService(
        meta_repo=cast(MetaPGRepo, meta_repo),
        source_repo=cast(SourceDorisRepo, source_repo),
        meta_index_service=cast(MetaIndexService, MagicMock()),
        asset_invalidator=cast(MetadataAssetInvalidator, invalidator),
        semantic_index_scheduler=cast(
            MetadataSemanticIndexScheduler,
            scheduler or MagicMock(),
        ),
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
        scheduler = MagicMock(spec=MetadataSemanticIndexScheduler)
        service = build_catalog_service(
            meta_repo,
            source_repo,
            invalidator,
            scheduler,
        )

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
        scheduler.enqueue_columns.assert_not_called()

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
        imported_column = ColumnInfo(
            t_name="orders",
            name="status",
            type="VARCHAR",
            description="订单状态",
            examples=[],
            alias=[],
            index_values=True,
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
        scheduler = MagicMock(spec=MetadataSemanticIndexScheduler)
        service = MetaImportService(
            meta_repo=cast(MetaPGRepo, meta_repo),
            source_repo=cast(SourceDorisRepo, source_repo),
            meta_index_service=cast(MetaIndexService, meta_index_service),
            asset_invalidator=cast(MetadataAssetInvalidator, invalidator),
            semantic_index_scheduler=cast(
                MetadataSemanticIndexScheduler,
                scheduler,
            ),
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
            AsyncMock(return_value=([imported], [imported_column], [])),
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
        scheduler.enqueue_columns.assert_called_once_with([("orders", "status")])
        scheduler.enqueue_metrics.assert_not_called()

    async def test_catalog_enqueues_semantic_sync_after_column_change(self) -> None:
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.get_table_info = AsyncMock(
            return_value=TableInfo(
                name="orders",
                role="fact",
                primary_key_columns=["id"],
                description="订单事实表",
            )
        )
        meta_repo.upsert_column_info = AsyncMock(return_value=True)
        source_repo = MagicMock(spec=SourceDorisRepo)
        source_repo.table_exists = AsyncMock(return_value=True)
        source_repo.get_column_types = AsyncMock(return_value={"status": "VARCHAR"})
        source_repo.get_column_values = AsyncMock(return_value=["已支付"])
        invalidator = MagicMock(spec=MetadataAssetInvalidator)
        invalidator.invalidate_assets = AsyncMock()
        scheduler = MagicMock(spec=MetadataSemanticIndexScheduler)
        submission = TaskSubmission(task_id="column-index-task")
        scheduler.enqueue_columns.return_value = submission
        service = build_catalog_service(
            meta_repo,
            source_repo,
            invalidator,
            scheduler,
        )

        result = await service.upsert_column_info(
            t_name="orders",
            c_name="status",
            description="订单状态",
            alias=["状态"],
            index_values=True,
        )

        self.assertIs(result, submission)
        scheduler.enqueue_columns.assert_called_once_with([("orders", "status")])

    async def test_catalog_returns_metric_semantic_sync_submission(self) -> None:
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.upsert_metric_info = AsyncMock(return_value=True)
        invalidator = MagicMock(spec=MetadataAssetInvalidator)
        scheduler = MagicMock(spec=MetadataSemanticIndexScheduler)
        submission = TaskSubmission(task_id="metric-index-task")
        scheduler.enqueue_metrics.return_value = submission
        service = build_catalog_service(
            meta_repo,
            MagicMock(spec=SourceDorisRepo),
            invalidator,
            scheduler,
        )

        result = await service.upsert_metric_info(
            MetricInfo(
                name="order_count",
                description="订单数量",
                relevant_columns=[],
                alias=["订单数"],
            )
        )

        self.assertIs(result, submission)
        scheduler.enqueue_metrics.assert_called_once_with(["order_count"])

    async def test_catalog_returns_no_submission_when_metric_is_unchanged(
        self,
    ) -> None:
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.upsert_metric_info = AsyncMock(return_value=False)
        invalidator = MagicMock(spec=MetadataAssetInvalidator)
        scheduler = MagicMock(spec=MetadataSemanticIndexScheduler)
        service = build_catalog_service(
            meta_repo,
            MagicMock(spec=SourceDorisRepo),
            invalidator,
            scheduler,
        )

        result = await service.upsert_metric_info(
            MetricInfo(
                name="order_count",
                description="订单数量",
                relevant_columns=[],
                alias=["订单数"],
            )
        )

        self.assertIsNone(result)
        scheduler.enqueue_metrics.assert_not_called()


if __name__ == "__main__":
    unittest.main()
