"""前后端 OpenAPI 协议测试。"""

import unittest
from uuid import uuid4

from pydantic import ValidationError

from app.assistant.api.chat.schemas import CreateConversationRequest
from app.identity.api.admin.schemas import SetUserAdministratorRequest
from app.identity.api.auth.schemas import LoginRequest
from app.metadata.api.meta.schemas import TableBatchDeleteRequest
from app.query.api.admin.schemas import QueryExperienceBatchRequest
from main import app
from scripts.development.generate_openapi_types import (
    OUTPUT_PATH,
    _render_openapi_types,
)


class ApiContractTests(unittest.TestCase):
    def test_public_request_models_reject_unknown_fields(self) -> None:
        cases = (
            (LoginRequest, {"identifier": "user", "password": "password"}),
            (SetUserAdministratorRequest, {"is_admin": True}),
            (CreateConversationRequest, {}),
            (TableBatchDeleteRequest, {"tables": ["orders"]}),
            (QueryExperienceBatchRequest, {"experience_ids": [uuid4()]}),
        )
        for model, payload in cases:
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model.model_validate({**payload, "unknown_field": True})

    def test_generated_types_match_openapi(self) -> None:
        self.assertEqual(
            _render_openapi_types(app.openapi()),
            OUTPUT_PATH.read_text(),
        )


if __name__ == "__main__":
    unittest.main()
