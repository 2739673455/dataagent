"""元数据写入枚举契约测试"""

import unittest
from typing import get_args

from pydantic import ValidationError

from app.metadata.api.meta.schemas import TableInfoRequest
from app.metadata.services.import_service import ImportMode
from app.shared.config.meta_config import TableRole


class MetadataEnumContractTest(unittest.TestCase):
    def test_import_modes_match_frontend_contract(self) -> None:
        self.assertEqual(
            {mode.value for mode in ImportMode},
            {"merge", "replace"},
        )

    def test_table_roles_match_frontend_contract(self) -> None:
        self.assertEqual(set(get_args(TableRole)), {"fact", "dim"})

    def test_all_import_modes_are_accepted(self) -> None:
        for value in ("merge", "replace"):
            with self.subTest(value=value):
                self.assertEqual(ImportMode(value).value, value)

    def test_all_table_roles_are_accepted(self) -> None:
        for value in ("fact", "dim"):
            with self.subTest(value=value):
                request = TableInfoRequest.model_validate(
                    {"role": value, "description": "table"}
                )
                self.assertEqual(request.role, value)

    def test_legacy_and_unknown_values_are_rejected(self) -> None:
        for value in ("overwrite", "dimension", "aggregate"):
            with self.subTest(value=value):
                if value == "overwrite":
                    with self.assertRaises(ValueError):
                        ImportMode(value)
                else:
                    with self.assertRaises(ValidationError):
                        TableInfoRequest.model_validate(
                            {"role": value, "description": "table"}
                        )


if __name__ == "__main__":
    unittest.main()
