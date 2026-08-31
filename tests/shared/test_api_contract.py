"""前后端 OpenAPI 协议测试"""

import unittest

from main import app
from scripts.development.generate_openapi_types import (
    OUTPUT_PATH,
    _render_openapi_types,
)


class ApiContractTests(unittest.TestCase):
    def test_generated_types_match_openapi(self) -> None:
        self.assertEqual(
            _render_openapi_types(app.openapi()),
            OUTPUT_PATH.read_text(),
        )


if __name__ == "__main__":
    unittest.main()
