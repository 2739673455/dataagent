"""元数据写入枚举契约测试"""

import unittest
from typing import get_args

from app.metadata.api.meta.schemas import TableInfoRequest
from app.metadata.models.catalog import TableInfo
from app.metadata.services.import_service import ImportMode
from app.shared.config.meta_config import TableConfig, TableRole


class MetadataEnumContractTest(unittest.TestCase):
    def test_import_modes_match_frontend_contract(self) -> None:
        self.assertEqual(
            {mode.value for mode in ImportMode},
            {"merge", "replace"},
        )

    def test_table_roles_match_frontend_contract(self) -> None:
        self.assertEqual(set(get_args(TableRole)), {"fact", "dim"})

    def test_all_table_roles_are_accepted(self) -> None:
        for value in ("fact", "dim"):
            with self.subTest(value=value):
                request = TableInfoRequest.model_validate(
                    {"role": value, "description": "table"}
                )
                self.assertEqual(request.role, value)

    def test_value_index_cursor_uses_flat_contract(self) -> None:
        request = TableInfoRequest.model_validate(
            {
                "role": "fact",
                "description": "订单事实表",
                "value_index_cursor_column": "dw_update_time",
            }
        )
        table = TableConfig.model_validate(
            {
                "name": "orders",
                "role": "fact",
                "description": "订单事实表",
                "value_index_cursor_column": "dw_update_time",
            }
        )

        self.assertEqual(request.value_index_cursor_column, "dw_update_time")
        self.assertEqual(table.value_index_cursor_column, "dw_update_time")
        self.assertIn("value_index_cursor_column", TableInfo.__table__.columns)


if __name__ == "__main__":
    unittest.main()
