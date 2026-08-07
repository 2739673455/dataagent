"""名称主键元数据集成测试"""

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock, MagicMock

import yaml
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import Session

from app.conf.meta_config import MetaConfig
from app.entities.meta import Base, ColumnInfo, ColumnMetric, MetricInfo, TableInfo
from app.errors.meta_error import (
    InvalidMetadataError,
    MetadataConflictError,
    MetadataNotFoundError,
)
from app.repositories.column_es_repo import ColumnESRepo
from app.repositories.meta_mysql_repo import MetaMySQLRepo
from app.repositories.metric_es_repo import MetricESRepo
from app.repositories.source_doris_repo import SourceDorisRepo
from app.routes.api.v1.meta.schemas import (
    ColumnIndexSyncRequest,
    ColumnInfoRequest,
    ColumnInfoResponse,
    MetricIndexSyncRequest,
    MetricInfoRequest,
    MetricInfoResponse,
    TableInfoRequest,
    TableInfoResponse,
)
from app.services.index_service import IndexService
from app.services.meta_import_service import ImportMode, MetaImportService
from app.services.meta_service import MetaService
from scripts.generate_meta_config import build_config, parse_ecommerce_schema


class _MetaRepo:
    """测试用内存元数据存储"""

    def __init__(self) -> None:
        self.tables: dict[str, TableInfo] = {}
        self.columns: dict[tuple[str, str], ColumnInfo] = {}
        self.metrics: dict[str, MetricInfo] = {}
        self.transaction_depth = 0

    @asynccontextmanager
    async def transaction(self):
        self.transaction_depth += 1
        try:
            yield
        finally:
            self.transaction_depth -= 1

    async def list_table_infos(self) -> list[TableInfo]:
        return list(self.tables.values())

    async def list_column_infos(self) -> list[ColumnInfo]:
        return list(self.columns.values())

    async def list_metric_infos(self) -> list[MetricInfo]:
        return list(self.metrics.values())

    async def get_table_info(self, t_name: str) -> TableInfo:
        try:
            return self.tables[t_name]
        except KeyError as exc:
            raise MetadataNotFoundError(
                detail=f"Table info not found: {t_name}"
            ) from exc

    async def get_column_info(self, t_name: str, c_name: str) -> ColumnInfo:
        try:
            return self.columns[(t_name, c_name)]
        except KeyError as exc:
            raise MetadataNotFoundError(
                detail=f"Column info not found: {t_name}.{c_name}"
            ) from exc

    async def get_metric_info(self, metric_name: str) -> MetricInfo:
        try:
            return self.metrics[metric_name]
        except KeyError as exc:
            raise MetadataNotFoundError(
                detail=f"Metric info not found: {metric_name}"
            ) from exc

    async def delete_metric_infos(self, metric_names: list[str]) -> None:
        for metric_name in metric_names:
            self.metrics.pop(metric_name, None)

    async def delete_column_infos(self, column_keys: list[tuple[str, str]]) -> None:
        for column_key in column_keys:
            self.columns.pop(column_key, None)

    async def delete_table_infos(self, table_names: list[str]) -> None:
        for t_name in table_names:
            self.tables.pop(t_name, None)

    async def upsert_table_info(
        self,
        table_info: TableInfo,
        *,
        force_version_increment: bool = False,
    ) -> None:
        self.tables[table_info.name] = table_info

    async def upsert_column_infos(
        self,
        column_infos: list[ColumnInfo],
        force_version_increment_keys: set[tuple[str, str]] | None = None,
    ) -> None:
        for column_info in column_infos:
            self.columns[(column_info.t_name, column_info.name)] = column_info

    async def upsert_column_info(self, column_info: ColumnInfo) -> None:
        self.columns[(column_info.t_name, column_info.name)] = column_info

    async def upsert_metric_info(
        self,
        metric_info: MetricInfo,
        *,
        force_version_increment: bool = False,
    ) -> None:
        self.metrics[metric_info.name] = metric_info


class _SourceRepo:
    """测试用业务表结构存储"""

    _columns: ClassVar[dict[str, dict[str, str]]] = {
        "users": {"id": "int"},
        "orders": {"id": "int", "user_id": "int"},
    }

    async def table_exists(self, t_name: str) -> bool:
        return t_name in self._columns

    async def get_primary_key_columns(self, t_name: str) -> list[str]:
        return ["id"]

    async def get_column_types(self, t_name: str) -> dict[str, str]:
        return self._columns[t_name]

    async def get_column_values(
        self,
        t_name: str,
        c_name: str,
        limit: int | None = None,
    ) -> list[Any]:
        return [1]


class MetadataNameKeyTest(unittest.IsolatedAsyncioTestCase):
    """验证 YAML 导入使用名称主键"""

    async def test_import_uses_names_and_composite_column_keys(self) -> None:
        config = MetaConfig.model_validate(
            {
                "tables": [
                    {
                        "name": "users",
                        "role": "dim",
                        "description": "用户",
                        "columns": [
                            {
                                "name": "id",
                                "description": "用户主键",
                                "index_values": False,
                            }
                        ],
                    },
                    {
                        "name": "orders",
                        "role": "fact",
                        "description": "订单",
                        "columns": [
                            {
                                "name": "id",
                                "description": "订单主键",
                                "index_values": False,
                            },
                            {
                                "name": "user_id",
                                "description": "用户主键",
                                "index_values": False,
                                "reference_t_name": "users",
                                "reference_c_name": "id",
                            },
                        ],
                    },
                ],
                "metrics": [
                    {
                        "name": "订单数",
                        "description": "订单总数",
                        "relevant_columns": [{"t_name": "orders", "c_name": "id"}],
                    }
                ],
            }
        )
        meta_repo = _MetaRepo()
        service = MetaImportService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, MagicMock(spec=IndexService)),
        )

        result = await service.import_metadata(config, ImportMode.MERGE, False)

        self.assertEqual(result.tables.created, ["orders", "users"])
        self.assertEqual(
            result.columns.created,
            [("orders", "id"), ("orders", "user_id"), ("users", "id")],
        )
        self.assertEqual(result.metrics.created, ["订单数"])
        self.assertEqual(
            meta_repo.columns[("orders", "user_id")].reference_t_name,
            "users",
        )
        self.assertEqual(meta_repo.tables["users"].primary_key_columns, ["id"])
        self.assertEqual(meta_repo.tables["orders"].primary_key_columns, ["id"])
        self.assertFalse(hasattr(meta_repo.tables["orders"], "id"))
        self.assertFalse(hasattr(meta_repo.columns[("orders", "id")], "id"))
        self.assertFalse(hasattr(meta_repo.metrics["订单数"], "id"))

    async def test_duplicate_table_name_is_rejected(self) -> None:
        config = MetaConfig.model_validate(
            {
                "tables": [
                    {
                        "name": "users",
                        "role": "dim",
                        "description": "用户一",
                        "columns": [
                            {
                                "name": "id",
                                "description": "用户主键",
                                "index_values": False,
                            }
                        ],
                    },
                    {
                        "name": "users",
                        "role": "dim",
                        "description": "用户二",
                        "columns": [
                            {
                                "name": "id",
                                "description": "用户主键",
                                "index_values": False,
                            }
                        ],
                    },
                ]
            }
        )
        service = MetaImportService(
            cast(MetaMySQLRepo, _MetaRepo()),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, MagicMock(spec=IndexService)),
        )

        with self.assertRaisesRegex(InvalidMetadataError, "Duplicate table name"):
            await service.import_metadata(config, ImportMode.MERGE, True)

    async def test_missing_source_table_is_rejected(self) -> None:
        config = MetaConfig.model_validate(
            {
                "tables": [
                    {
                        "name": "missing_table",
                        "role": "dim",
                        "description": "不存在的表",
                    }
                ]
            }
        )
        service = MetaImportService(
            cast(MetaMySQLRepo, _MetaRepo()),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, MagicMock(spec=IndexService)),
        )

        with self.assertRaisesRegex(
            InvalidMetadataError,
            "Source table not found: missing_table",
        ):
            await service.import_metadata(config, ImportMode.MERGE, True)

    async def test_primary_key_column_can_be_omitted_from_yaml(self) -> None:
        config = MetaConfig.model_validate(
            {
                "tables": [
                    {
                        "name": "users",
                        "role": "dim",
                        "description": "用户",
                        "columns": [],
                    }
                ]
            }
        )
        meta_repo = _MetaRepo()
        service = MetaImportService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, MagicMock(spec=IndexService)),
        )

        await service.import_metadata(config, ImportMode.MERGE, False)

        self.assertEqual(meta_repo.tables["users"].primary_key_columns, ["id"])
        self.assertEqual(meta_repo.columns, {})

    async def test_replace_deletes_external_indexes_before_metadata(self) -> None:
        config = MetaConfig.model_validate(
            {
                "tables": [
                    {
                        "name": "users",
                        "role": "dim",
                        "description": "用户",
                        "columns": [
                            {
                                "name": "id",
                                "description": "用户主键",
                                "index_values": False,
                            }
                        ],
                    }
                ]
            }
        )
        meta_repo = _MetaRepo()
        meta_repo.tables = {
            "users": TableInfo(
                name="users",
                role="dim",
                primary_key_columns=["id"],
                description="用户",
            ),
            "orders": TableInfo(
                name="orders",
                role="fact",
                primary_key_columns=["id"],
                description="订单",
            ),
        }
        meta_repo.columns = {
            ("users", "id"): ColumnInfo(
                t_name="users",
                name="id",
                type="int",
                description="用户主键",
                examples=[1],
                alias=[],
                index_values=False,
            ),
            ("orders", "id"): ColumnInfo(
                t_name="orders",
                name="id",
                type="int",
                description="订单主键",
                examples=[1],
                alias=[],
                index_values=True,
            ),
        }
        meta_repo.metrics["订单数"] = MetricInfo(
            name="订单数",
            description="订单总数",
            alias=[],
            relevant_columns=[{"t_name": "orders", "c_name": "id"}],
        )
        index_service = MagicMock(spec=IndexService)

        async def delete_column_indexes(column_keys):
            self.assertEqual(meta_repo.transaction_depth, 0)

        async def delete_metric_indexes(metric_names):
            self.assertEqual(meta_repo.transaction_depth, 0)

        index_service.delete_column_indexes = AsyncMock(
            side_effect=delete_column_indexes
        )
        index_service.delete_metric_indexes = AsyncMock(
            side_effect=delete_metric_indexes
        )
        service = MetaImportService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, index_service),
        )

        result = await service.import_metadata(config, ImportMode.REPLACE, False)

        self.assertEqual(result.tables.deleted, ["orders"])
        self.assertEqual(result.columns.deleted, [("orders", "id")])
        self.assertEqual(result.metrics.deleted, ["订单数"])
        index_service.delete_metric_indexes.assert_awaited_once_with(["订单数"])
        index_service.delete_column_indexes.assert_awaited_once_with([("orders", "id")])
        self.assertNotIn("orders", meta_repo.tables)
        self.assertNotIn(("orders", "id"), meta_repo.columns)
        self.assertNotIn("订单数", meta_repo.metrics)

    async def test_metadata_versions_increment_only_when_content_changes(self) -> None:
        synced_at = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        existing = ColumnInfo(
            t_name="users",
            name="id",
            type="int",
            description="用户主键",
            examples=[],
            alias=[],
            index_values=False,
            meta_version=3,
            index_version=2,
            value_index_synced_at=synced_at,
            value_index_sync_status="succeeded",
        )
        changed = ColumnInfo(
            t_name="users",
            name="id",
            type="bigint",
            description="用户主键",
            examples=[],
            alias=[],
            index_values=False,
        )
        unchanged = ColumnInfo(
            t_name="users",
            name="id",
            type="int",
            description="用户主键",
            examples=[],
            alias=[],
            index_values=False,
        )
        forced = ColumnInfo(
            t_name="users",
            name="id",
            type="int",
            description="用户主键",
            examples=[],
            alias=[],
            index_values=False,
        )
        session = MagicMock(spec=AsyncSession)
        session.get = AsyncMock(side_effect=[existing, existing, existing])
        session.merge = AsyncMock()
        repository = MetaMySQLRepo(cast(AsyncSession, session))

        await repository.upsert_column_info(changed)
        await repository.upsert_column_info(unchanged)
        await repository.upsert_column_info(forced, force_version_increment=True)

        self.assertEqual(
            (
                changed.meta_version,
                changed.index_version,
                changed.value_index_synced_at,
                changed.value_index_sync_status,
            ),
            (4, 2, synced_at, "succeeded"),
        )
        self.assertEqual(
            (
                unchanged.meta_version,
                unchanged.index_version,
                unchanged.value_index_synced_at,
                unchanged.value_index_sync_status,
            ),
            (3, 2, synced_at, "succeeded"),
        )
        self.assertEqual(
            (
                forced.meta_version,
                forced.index_version,
                forced.value_index_synced_at,
                forced.value_index_sync_status,
            ),
            (4, 2, synced_at, "succeeded"),
        )

    async def test_table_version_does_not_require_index_version(self) -> None:
        existing = TableInfo(
            name="users",
            role="dim",
            primary_key_columns=["id"],
            description="用户",
            meta_version=3,
        )
        changed = TableInfo(
            name="users",
            role="dim",
            primary_key_columns=["id"],
            description="用户维度表",
        )
        session = MagicMock(spec=AsyncSession)
        session.get = AsyncMock(return_value=existing)
        session.merge = AsyncMock()
        repository = MetaMySQLRepo(cast(AsyncSession, session))

        await repository.upsert_table_info(changed)

        self.assertEqual(changed.meta_version, 4)
        self.assertFalse(hasattr(changed, "index_version"))

    async def test_index_version_follows_synchronized_metadata_version(self) -> None:
        column_info = ColumnInfo(
            t_name="users",
            name="id",
            type="int",
            description="用户主键",
            examples=[],
            alias=[],
            index_values=False,
            meta_version=4,
            index_version=2,
        )
        metric_info = MetricInfo(
            name="用户数",
            description="用户总数",
            alias=[],
            meta_version=5,
            index_version=3,
        )

        column_payload = ColumnESRepo._to_payload(column_info)
        metric_payload = MetricESRepo._to_payload(metric_info)
        MetaMySQLRepo.mark_column_indexed(column_info)
        MetaMySQLRepo.mark_metric_indexed(metric_info)

        self.assertEqual(column_payload["index_version"], 4)
        self.assertNotIn("value_index_synced_at", column_payload)
        self.assertNotIn("value_index_sync_status", column_payload)
        self.assertEqual(metric_payload["index_version"], 5)
        self.assertEqual(column_info.index_version, column_info.meta_version)
        self.assertEqual(metric_info.index_version, metric_info.meta_version)


class SourceDorisRepoTest(unittest.IsolatedAsyncioTestCase):
    """验证 Doris 源数据库访问"""

    async def test_column_values_are_streamed_in_batches(self) -> None:
        stream_result = MagicMock()

        async def partitions(batch_size: int):
            self.assertEqual(batch_size, 2)
            yield [1, 2]
            yield [3]

        stream_result.partitions = partitions
        connection = MagicMock(spec=AsyncConnection)
        connection.stream_scalars = AsyncMock(return_value=stream_result)
        repository = SourceDorisRepo(cast(AsyncConnection, connection))

        batches = [
            batch
            async for batch in repository.iter_column_value_batches(
                "users",
                "id",
                batch_size=2,
            )
        ]

        self.assertEqual(batches, [[1, 2], [3]])
        self.assertEqual(
            connection.stream_scalars.await_args.kwargs["execution_options"],
            {"yield_per": 2},
        )

    async def test_table_exists_and_primary_key_order(self) -> None:
        exists_result = MagicMock()
        exists_result.scalar.return_value = 1
        primary_key_result = MagicMock()
        primary_key_result.scalars.return_value.fetchall.return_value = [
            "tenant_id",
            "id",
        ]
        connection = MagicMock(spec=AsyncConnection)
        connection.execute = AsyncMock(side_effect=[exists_result, primary_key_result])
        repository = SourceDorisRepo(cast(AsyncConnection, connection))

        self.assertTrue(await repository.table_exists("orders"))
        self.assertEqual(
            await repository.get_primary_key_columns("orders"),
            ["tenant_id", "id"],
        )
        for call in connection.execute.await_args_list:
            self.assertEqual(call.args[1], {"table_name": "orders"})
        primary_key_sql = str(connection.execute.await_args_list[1].args[0])
        self.assertIn("column_key = 'UNI'", primary_key_sql)


class MetaServiceIntegrityTest(unittest.IsolatedAsyncioTestCase):
    """验证元数据写入完整性"""

    async def test_query_table_column_and_metric_metadata(self) -> None:
        meta_repo = _MetaRepo()
        meta_repo.tables["users"] = TableInfo(
            name="users",
            role="dim",
            primary_key_columns=["id"],
            description="用户",
            meta_version=2,
        )
        meta_repo.columns[("users", "id")] = ColumnInfo(
            t_name="users",
            name="id",
            type="int",
            description="用户主键",
            examples=[1],
            alias=["用户编号"],
            index_values=False,
            meta_version=3,
            index_version=2,
            value_index_synced_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            value_index_sync_status="succeeded",
        )
        meta_repo.metrics["用户数"] = MetricInfo(
            name="用户数",
            description="用户总数",
            alias=["客户数"],
            relevant_columns=[{"t_name": "users", "c_name": "id"}],
            meta_version=4,
            index_version=3,
        )
        service = MetaService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, MagicMock(spec=IndexService)),
        )

        tables = await service.list_table_infos()
        columns = await service.list_column_infos("users")
        metrics = await service.list_metric_infos()

        self.assertEqual([item.name for item in tables], ["users"])
        self.assertEqual([item.name for item in columns], ["id"])
        self.assertEqual([item.name for item in metrics], ["用户数"])
        self.assertEqual(TableInfoResponse.model_validate(tables[0]).meta_version, 2)
        self.assertEqual(
            ColumnInfoResponse.model_validate(columns[0]).index_version,
            2,
        )
        self.assertEqual(
            ColumnInfoResponse.model_validate(columns[0]).value_index_sync_status,
            "succeeded",
        )
        self.assertEqual(
            MetricInfoResponse.model_validate(metrics[0]).relevant_columns[0].c_name,
            "id",
        )

    async def test_column_requires_existing_table(self) -> None:
        meta_repo = _MetaRepo()
        service = MetaService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, MagicMock(spec=IndexService)),
        )
        with self.assertRaisesRegex(
            InvalidMetadataError,
            "Table info not found for column: users.id",
        ):
            await service.upsert_column_info(
                t_name="users",
                c_name="id",
                description="用户主键",
                alias=[],
                index_values=False,
            )

        self.assertEqual(meta_repo.columns, {})

    async def test_column_is_written_when_table_exists(self) -> None:
        meta_repo = _MetaRepo()
        meta_repo.tables["users"] = TableInfo(
            name="users",
            role="dim",
            primary_key_columns=["id"],
            description="用户",
        )
        service = MetaService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, MagicMock(spec=IndexService)),
        )

        await service.upsert_column_info(
            t_name="users",
            c_name="id",
            description="用户主键",
            alias=[],
            index_values=False,
        )

        column_info = meta_repo.columns[("users", "id")]
        self.assertEqual(column_info.type, "int")
        self.assertEqual(column_info.examples, [1])

    async def test_table_primary_key_is_loaded_from_source_database(self) -> None:
        meta_repo = _MetaRepo()
        service = MetaService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, MagicMock(spec=IndexService)),
        )

        await service.upsert_table_info("users", "dim", "用户")

        self.assertEqual(meta_repo.tables["users"].primary_key_columns, ["id"])

    async def test_missing_source_table_cannot_be_written(self) -> None:
        meta_repo = _MetaRepo()
        service = MetaService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, MagicMock(spec=IndexService)),
        )

        with self.assertRaisesRegex(
            InvalidMetadataError,
            "Source table not found: missing_table",
        ):
            await service.upsert_table_info("missing_table", "dim", "不存在")

        self.assertEqual(meta_repo.tables, {})

    async def test_delete_table_cleans_column_indexes(self) -> None:
        meta_repo = _MetaRepo()
        meta_repo.tables["orders"] = TableInfo(
            name="orders",
            role="fact",
            primary_key_columns=["id"],
            description="订单",
        )
        meta_repo.columns = {
            ("orders", "id"): ColumnInfo(
                t_name="orders",
                name="id",
                type="int",
                description="订单主键",
                examples=[1],
                alias=[],
                index_values=False,
            ),
            ("orders", "user_id"): ColumnInfo(
                t_name="orders",
                name="user_id",
                type="int",
                description="用户主键",
                examples=[1],
                alias=[],
                index_values=True,
            ),
        }
        index_service = MagicMock(spec=IndexService)

        async def delete_column_indexes(column_keys):
            self.assertEqual(meta_repo.transaction_depth, 0)

        index_service.delete_column_indexes = AsyncMock(
            side_effect=delete_column_indexes
        )
        service = MetaService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, index_service),
        )

        await service.delete_table_info("orders")

        index_service.delete_column_indexes.assert_awaited_once_with(
            [("orders", "id"), ("orders", "user_id")]
        )
        self.assertNotIn("orders", meta_repo.tables)
        self.assertEqual(meta_repo.columns, {})

    async def test_delete_column_cleans_indexes(self) -> None:
        meta_repo = _MetaRepo()
        meta_repo.tables["users"] = TableInfo(
            name="users",
            role="dim",
            primary_key_columns=["id"],
            description="用户",
        )
        meta_repo.columns[("users", "id")] = ColumnInfo(
            t_name="users",
            name="id",
            type="int",
            description="用户主键",
            examples=[1],
            alias=[],
            index_values=False,
        )
        index_service = MagicMock(spec=IndexService)
        index_service.delete_column_indexes = AsyncMock()
        service = MetaService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, index_service),
        )

        await service.delete_column_info("users", "id")

        index_service.delete_column_indexes.assert_awaited_once_with([("users", "id")])
        self.assertEqual(meta_repo.columns, {})

    async def test_delete_metric_cleans_index(self) -> None:
        meta_repo = _MetaRepo()
        meta_repo.metrics["用户数"] = MetricInfo(
            name="用户数",
            description="用户总数",
            alias=[],
        )
        index_service = MagicMock(spec=IndexService)
        index_service.delete_metric_indexes = AsyncMock()
        service = MetaService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, index_service),
        )

        await service.delete_metric_info("用户数")

        index_service.delete_metric_indexes.assert_awaited_once_with(["用户数"])
        self.assertEqual(meta_repo.metrics, {})

    async def test_delete_referenced_column_is_rejected(self) -> None:
        meta_repo = _MetaRepo()
        meta_repo.columns = {
            ("users", "id"): ColumnInfo(
                t_name="users",
                name="id",
                type="int",
                description="用户主键",
                examples=[1],
                alias=[],
                index_values=False,
            ),
            ("orders", "user_id"): ColumnInfo(
                t_name="orders",
                name="user_id",
                type="int",
                description="用户主键",
                examples=[1],
                alias=[],
                index_values=False,
                reference_t_name="users",
                reference_c_name="id",
            ),
        }
        meta_repo.metrics["用户数"] = MetricInfo(
            name="用户数",
            description="用户总数",
            alias=[],
            relevant_columns=[{"t_name": "users", "c_name": "id"}],
        )
        index_service = MagicMock(spec=IndexService)
        index_service.delete_column_indexes = AsyncMock()
        service = MetaService(
            cast(MetaMySQLRepo, meta_repo),
            cast(SourceDorisRepo, _SourceRepo()),
            cast(IndexService, index_service),
        )

        with self.assertRaisesRegex(
            MetadataConflictError,
            "referenced by columns: orders.user_id; used by metrics: 用户数",
        ):
            await service.delete_column_info("users", "id")

        index_service.delete_column_indexes.assert_not_awaited()
        self.assertIn(("users", "id"), meta_repo.columns)


class MetadataSchemaTest(unittest.TestCase):
    """验证关系数据库名称主键结构"""

    def test_orm_schema_and_foreign_keys_are_executable(self) -> None:
        self.assertEqual(
            [column.name for column in TableInfo.__table__.primary_key],
            ["name"],
        )
        self.assertEqual(
            [column.name for column in ColumnInfo.__table__.primary_key],
            ["t_name", "name"],
        )
        self.assertEqual(
            [column.name for column in ColumnMetric.__table__.primary_key],
            ["t_name", "c_name", "metric_name"],
        )
        self.assertNotIn("relevant_columns", MetricInfo.__table__.columns)
        for model in (TableInfo, ColumnInfo, MetricInfo):
            self.assertIn("meta_version", model.__table__.columns)
        self.assertNotIn("index_version", TableInfo.__table__.columns)
        for model in (ColumnInfo, MetricInfo):
            self.assertIn("index_version", model.__table__.columns)
        self.assertIn("value_index_synced_at", ColumnInfo.__table__.columns)
        self.assertIn("value_index_sync_status", ColumnInfo.__table__.columns)

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(
                TableInfo(
                    name="users",
                    role="dim",
                    primary_key_columns=["id"],
                    description="用户",
                )
            )
            session.add_all(
                [
                    ColumnInfo(
                        t_name="users",
                        name="id",
                        type="int",
                        description="用户主键",
                        examples=[],
                        alias=[],
                        index_values=False,
                    ),
                    ColumnInfo(
                        t_name="users",
                        name="manager_id",
                        type="int",
                        description="上级用户",
                        examples=[],
                        alias=[],
                        index_values=False,
                        reference_t_name="users",
                        reference_c_name="id",
                    ),
                ]
            )
            session.add(MetricInfo(name="用户数", description="用户总数", alias=[]))
            session.add(
                ColumnMetric(
                    t_name="users",
                    c_name="id",
                    metric_name="用户数",
                )
            )
            session.commit()

            table_info = session.get(TableInfo, "users")
            column_info = session.get(ColumnInfo, ("users", "id"))
            metric_info = session.get(MetricInfo, "用户数")
            assert table_info is not None
            assert column_info is not None
            assert metric_info is not None
            self.assertEqual(table_info.meta_version, 1)
            self.assertEqual(
                (
                    column_info.meta_version,
                    column_info.index_version,
                    column_info.value_index_synced_at,
                    column_info.value_index_sync_status,
                ),
                (1, 0, None, None),
            )
            self.assertEqual(
                (metric_info.meta_version, metric_info.index_version),
                (1, 0),
            )

    def test_example_yaml_uses_name_references(self) -> None:
        raw_config = yaml.safe_load(Path("conf/meta_config.yaml").read_text())
        config = MetaConfig.model_validate(raw_config)

        self.assertEqual(raw_config, build_config())
        self.assertTrue(config.tables)
        self.assertTrue(config.metrics)
        self.assertNotIn("version", raw_config)
        self.assertNotIn("id", raw_config["tables"][0])
        self.assertTrue(
            all("primary_key_columns" not in table for table in raw_config["tables"])
        )
        source_tables = parse_ecommerce_schema()
        self.assertEqual({table.name for table in config.tables}, set(source_tables))
        self.assertTrue(
            all(
                {column.name for column in table.columns}
                == {
                    source_column["name"]
                    for source_column in source_tables[table.name]["columns"]
                }
                for table in config.tables
            )
        )
        self.assertTrue(
            all(
                table.name in source_tables
                and all(
                    column.name
                    in {
                        source_column["name"]
                        for source_column in source_tables[table.name]["columns"]
                    }
                    for column in table.columns
                )
                for table in config.tables
            )
        )
        column_keys = {
            (table.name, column.name)
            for table in config.tables
            for column in table.columns
        }
        self.assertTrue(
            all(
                column.reference_t_name is None
                or (
                    column.reference_t_name,
                    column.reference_c_name,
                )
                in column_keys
                for table in config.tables
                for column in table.columns
            )
        )
        self.assertTrue(
            all(
                (reference.t_name, reference.c_name) in column_keys
                for metric in config.metrics
                for reference in metric.relevant_columns
            )
        )

    def test_table_request_rejects_primary_key_columns(self) -> None:
        with self.assertRaises(PydanticValidationError):
            TableInfoRequest.model_validate(
                {
                    "role": "dim",
                    "description": "用户",
                    "primary_key_columns": ["id"],
                }
            )

    def test_table_request_rejects_invalid_role(self) -> None:
        with self.assertRaises(PydanticValidationError):
            TableInfoRequest.model_validate(
                {
                    "role": "invalid",
                    "description": "用户",
                }
            )

    def test_metric_request_rejects_extra_fields(self) -> None:
        with self.assertRaises(PydanticValidationError):
            MetricInfoRequest.model_validate(
                {
                    "description": "用户总数",
                    "unknown": True,
                }
            )

    def test_index_sync_requests_reject_extra_fields(self) -> None:
        with self.assertRaises(PydanticValidationError):
            ColumnIndexSyncRequest.model_validate(
                {
                    "columns": [{"t_name": "users", "c_name": "id"}],
                    "unknown": True,
                }
            )
        with self.assertRaises(PydanticValidationError):
            MetricIndexSyncRequest.model_validate(
                {
                    "metrics": ["用户数"],
                    "unknown": True,
                }
            )

    def test_metadata_config_rejects_version(self) -> None:
        with self.assertRaises(PydanticValidationError):
            MetaConfig.model_validate({"version": 1})

    def test_column_request_rejects_type_and_examples(self) -> None:
        with self.assertRaises(PydanticValidationError):
            ColumnInfoRequest.model_validate(
                {
                    "type": "int",
                    "examples": [1],
                    "description": "用户主键",
                    "index_values": False,
                }
            )


if __name__ == "__main__":
    unittest.main()
