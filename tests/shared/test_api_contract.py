"""前后端 OpenAPI 协议测试"""

import unittest

from main import app
from scripts.generate_openapi_types import OUTPUT_PATH, render_openapi_types


class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.openapi = app.openapi()

    def test_generated_types_match_openapi(self) -> None:
        self.assertEqual(
            render_openapi_types(self.openapi),
            OUTPUT_PATH.read_text(),
        )

    def test_contract_covers_enums_nullable_fields_and_errors(self) -> None:
        schemas = self.openapi["components"]["schemas"]
        self.assertEqual(
            ["RESTRICTIVE", "PERMISSIVE"],
            schemas["RowPolicyRequest"]["properties"]["policy_type"]["enum"],
        )
        self.assertEqual(
            ["fact", "dim"],
            schemas["TableInfoResponse"]["properties"]["role"]["enum"],
        )
        self.assertIn("type", schemas["TextContent"]["required"])
        self.assertEqual(
            {
                "name",
                "is_attached",
                "description",
                "query_user",
                "workload_group",
            },
            set(schemas["DiscoveredDorisRoleResponse"]["required"]),
        )
        for event_name in (
            "ChatStreamMessageEvent",
            "ChatStreamErrorEvent",
            "ChatStreamDoneEvent",
        ):
            self.assertIn("type", schemas[event_name]["required"])
        doris_role = schemas["UserResponse"]["properties"]["doris_role"]
        self.assertIn({"type": "null"}, doris_role["anyOf"])

        problem = schemas["ProblemDetails"]
        self.assertEqual(
            {"type", "title", "status"},
            set(problem["required"]),
        )
        self.assertTrue(problem["additionalProperties"])
        login_responses = self.openapi["paths"]["/api/v1/auth/login"]["post"][
            "responses"
        ]
        self.assertEqual(
            {"$ref": "#/components/schemas/ProblemDetails"},
            login_responses["default"]["content"]["application/problem+json"][
                "schema"
            ],
        )
        self.assertEqual(
            {"$ref": "#/components/schemas/ProblemDetails"},
            login_responses["422"]["content"]["application/problem+json"]["schema"],
        )

        stream_response = self.openapi["paths"]["/api/v1/chat/stream"]["post"][
            "responses"
        ]["200"]
        self.assertEqual(
            {"$ref": "#/components/schemas/ChatStreamEvent"},
            stream_response["content"]["text/event-stream"]["schema"],
        )
        self.assertEqual(
            3,
            len(schemas["ChatStreamEvent"]["oneOf"]),
        )

    def test_contract_covers_pagination_parameters(self) -> None:
        operation = self.openapi["paths"]["/api/v1/admin/users"]["get"]
        query_parameters = {
            parameter["name"]: parameter for parameter in operation["parameters"]
        }
        self.assertEqual(1, query_parameters["limit"]["schema"]["minimum"])
        self.assertEqual(500, query_parameters["limit"]["schema"]["maximum"])
        self.assertEqual(100, query_parameters["limit"]["schema"]["default"])
        self.assertEqual(0, query_parameters["offset"]["schema"]["minimum"])
        self.assertEqual(0, query_parameters["offset"]["schema"]["default"])


if __name__ == "__main__":
    unittest.main()
