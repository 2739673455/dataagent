import json
import unittest

from langchain_core.messages import ToolMessage

from app.clients.docker_sandbox_manager import normalize_attachment_path
from app.mappers.message_mapper import (
    agent_chunk_to_schemas,
    langchain_message_to_schema,
)


def _delegate_payload() -> dict[str, object]:
    return {
        "status": "completed",
        "analysis_id": "sales-review",
        "agent_type": "visualizer",
        "session_id": "chart-1",
        "summary": "Chart generated",
        "findings": ["Revenue increased"],
        "artifacts": [
            {
                "path": (
                    "/analyses/sales-review/sessions/visualizer/chart-1/report.html"
                ),
                "media_type": "text/html",
                "description": "Interactive report",
            }
        ],
        "repair_requests": [],
        "confidence": "high",
        "limitations": [],
    }


class MessageMapperArtifactTest(unittest.TestCase):
    def test_delegate_artifacts_are_restored_from_history(self) -> None:
        message = ToolMessage(
            id="message-1",
            name="delegate_agent",
            tool_call_id="call-1",
            content=json.dumps(_delegate_payload()),
        )

        schema = langchain_message_to_schema(message)

        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertEqual(schema.role, "tool")
        self.assertEqual(len(schema.attachments or []), 1)
        attachment = (schema.attachments or [])[0]
        self.assertEqual(
            attachment.f_path,
            "analyses/sales-review/sessions/visualizer/chart-1/report.html",
        )
        self.assertEqual(attachment.media_type, "text/html")
        self.assertEqual(attachment.description, "Interactive report")
        self.assertEqual(
            normalize_attachment_path(attachment.f_path),
            attachment.f_path,
        )

    def test_delegate_artifacts_are_in_stream_updates(self) -> None:
        message = ToolMessage(
            id="message-1",
            name="delegate_agent",
            tool_call_id="call-1",
            content=json.dumps(_delegate_payload()),
        )

        schemas = agent_chunk_to_schemas({"tools": {"messages": [message]}})

        self.assertEqual(len(schemas), 1)
        self.assertEqual(len(schemas[0].attachments or []), 1)

    def test_invalid_delegate_artifact_payload_is_not_exposed(self) -> None:
        payload = _delegate_payload()
        payload["artifacts"] = [{"path": "/analyses/../secret"}]
        message = ToolMessage(
            id="message-1",
            name="delegate_agent",
            tool_call_id="call-1",
            content=json.dumps(payload),
        )

        schema = langchain_message_to_schema(message)

        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertIsNone(schema.attachments)


if __name__ == "__main__":
    unittest.main()
