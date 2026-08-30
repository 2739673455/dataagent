"""元数据写入字段契约测试"""

import unittest

from app.metadata.api.meta.schemas import TableInfoRequest
from app.metadata.models.catalog import TableInfo
from app.shared.config.meta_config import TableConfig


class MetadataFieldContractTest(unittest.TestCase):
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
