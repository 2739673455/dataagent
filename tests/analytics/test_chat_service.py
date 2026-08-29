"""聊天回合与 Planner 自动续写测试"""

from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from app.analytics.agents.contracts import (
    MESSAGE_CREATED_AT_KEY,
    ConversationAgentRuntime,
    PlannerTurnContext,
    SubagentMessageActivity,
    SubagentStatusActivity,
)
from app.analytics.agents.message_timestamp_middleware import (
    MessageTimestampMiddleware,
)
from app.analytics.agents.user_message_metadata import (
    USER_MESSAGE_METADATA_KEY,
    UserMessageMetadata,
)
from app.analytics.api.chat import schemas as chat_schema
from app.analytics.services import chat as chat_service
from app.sandbox.paths import normalize_attachment_path

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


class UserMessageRequestTest(unittest.TestCase):
    def test_rejects_response_only_fields(self) -> None:
        for field, value in (
            ("message_id", "client-message"),
            ("role", "user"),
            ("finish_reason", "stop"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                chat_schema.UserMessageRequest.model_validate(
                    {
                        "parts": [{"type": "text", "text": "analyze"}],
                        field: value,
                    }
                )

    def test_rejects_tool_parts_and_empty_messages(self) -> None:
        with self.assertRaises(ValidationError):
            chat_schema.UserMessageRequest.model_validate(
                {
                    "parts": [
                        {
                            "type": "tool_call",
                            "tool_call_id": "call-1",
                            "name": "search",
                            "args": {},
                        }
                    ]
                }
            )
        with self.assertRaises(ValidationError):
            chat_schema.UserMessageRequest(parts=[])
        with self.assertRaises(ValidationError):
            chat_schema.UserMessageRequest.model_validate(
                {
                    "parts": [],
                    "attachments": [
                        {
                            "f_path": "uploads/report.csv",
                            "media_type": "text/csv",
                        }
                    ],
                }
            )

    def test_accepts_uploaded_attachment_references(self) -> None:
        message = chat_schema.UserMessageRequest(
            parts=[],
            attachments=[chat_schema.AttachmentReference(f_path="uploads/report.csv")],
        )

        self.assertIsNotNone(message.attachments)
        assert message.attachments is not None
        self.assertEqual(message.attachments[0].f_path, "uploads/report.csv")


class MessageTimestampTest(unittest.IsolatedAsyncioTestCase):
    async def test_user_message_creation_time_is_persisted(self) -> None:
        message = await chat_service._schema_to_human_message(
            MagicMock(),
            chat_schema.UserMessageRequest(
                parts=[chat_schema.TextContent(type="text", text="analyze")]
            ),
            7,
            _CONVERSATION_ID,
        )

        metadata = chat_schema.MessageResponse.model_validate(
            message.additional_kwargs[chat_service.MESSAGE_PAYLOAD_KEY]
        )
        self.assertIsNotNone(metadata.created_at)
        private_metadata = UserMessageMetadata.model_validate(
            message.additional_kwargs[USER_MESSAGE_METADATA_KEY]
        )
        self.assertEqual(metadata.created_at, private_metadata.received_at)

    async def test_private_user_message_metadata_is_not_exposed_by_api_schema(
        self,
    ) -> None:
        message = await chat_service._schema_to_human_message(
            MagicMock(),
            chat_schema.UserMessageRequest(
                parts=[chat_schema.TextContent(type="text", text="analyze")]
            ),
            7,
            _CONVERSATION_ID,
        )

        response = chat_service._langchain_message_to_schema(message)

        self.assertIsNotNone(response)
        assert response is not None
        payload = response.model_dump(mode="json")
        self.assertNotIn(USER_MESSAGE_METADATA_KEY, payload)
        self.assertIsNotNone(payload["created_at"])
        self.assertNotIn("<message_metadata>", json.dumps(payload))

    async def test_model_response_creation_time_is_persisted(self) -> None:
        middleware = MessageTimestampMiddleware()
        response_message = AIMessage(content="result")

        async def handler(_: Any) -> ModelResponse[Any]:
            return ModelResponse(result=[response_message])

        await middleware.awrap_model_call(MagicMock(), handler)

        self.assertIn(MESSAGE_CREATED_AT_KEY, response_message.additional_kwargs)


class _RepeatingPlanner:
    """重复返回同一 finish reason 并记录 Planner 配置"""

    def __init__(self, finish_reason: str) -> None:
        self.finish_reason = finish_reason
        self.configs: list[RunnableConfig] = []
        self.input_sizes: list[int] = []

    async def astream(
        self,
        input: dict[str, list[Any]],
        config: RunnableConfig,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        del kwargs
        self.configs.append(config)
        self.input_sizes.append(len(input["messages"]))
        yield {
            "type": "updates",
            "ns": (),
            "data": {
                "model": {
                    "messages": [
                        AIMessage(
                            id=f"response-{len(self.configs)}",
                            content="partial response",
                            response_metadata={"finish_reason": self.finish_reason},
                        )
                    ]
                }
            },
        }


class _TurnManagerStub:
    """记录一个聊天回合进入的执行上下文次数"""

    def __init__(
        self,
        runtime: ConversationAgentRuntime,
        turn_context: PlannerTurnContext,
    ) -> None:
        self.runtime = runtime
        self.turn_context = turn_context
        self.execution_count = 0

    async def get_conversation_runtime(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> ConversationAgentRuntime:
        if user_id != self.turn_context.user_id:
            raise AssertionError("unexpected user_id")
        if conversation_id != self.turn_context.conversation_id:
            raise AssertionError("unexpected conversation_id")
        return self.runtime

    @asynccontextmanager
    async def execution(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        runtime: ConversationAgentRuntime,
    ) -> AsyncGenerator[PlannerTurnContext, None]:
        if user_id != self.turn_context.user_id:
            raise AssertionError("unexpected user_id")
        if conversation_id != self.turn_context.conversation_id:
            raise AssertionError("unexpected conversation_id")
        if runtime is not self.runtime:
            raise AssertionError("unexpected runtime")
        self.execution_count += 1
        yield self.turn_context


class PlannerContinuationTest(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_finish_reason_reuses_turn_budget_and_hits_limit(
        self,
    ) -> None:
        for finish_reason in ("length", "content_filter"):
            with self.subTest(finish_reason=finish_reason):
                await self._assert_repeated_finish_reason_is_bounded(finish_reason)

    async def _assert_repeated_finish_reason_is_bounded(
        self,
        finish_reason: str,
    ) -> None:
        planner = _RepeatingPlanner(finish_reason)
        runtime_mock = MagicMock()
        runtime_mock.planner = planner
        runtime = cast(ConversationAgentRuntime, runtime_mock)
        turn_context = PlannerTurnContext(
            user_id=7,
            conversation_id=_CONVERSATION_ID,
            max_continuations=2,
        )
        manager = _TurnManagerStub(runtime, turn_context)

        user_message = chat_schema.UserMessageRequest(
            parts=[chat_schema.TextContent(type="text", text="analyze")],
        )
        events: list[chat_schema.ChatStreamEventPayload] = []
        with (
            patch.object(
                chat_service,
                "_schema_to_human_message",
                new=AsyncMock(return_value=HumanMessage(content="analyze")),
            ),
            self.assertRaisesRegex(
                chat_service.PlannerContinuationLimitError,
                "连续续写次数超过上限",
            ),
        ):
            async for event in chat_service.run_agent_turn(
                manager,
                MagicMock(),
                7,
                _CONVERSATION_ID,
                user_message,
                asyncio.Event(),
            ):
                events.append(event)

        self.assertEqual(manager.execution_count, 1)
        self.assertEqual(len(planner.configs), 3)
        self.assertEqual(planner.input_sizes, [1, 0, 0])
        self.assertEqual(len(events), 3)
        self.assertTrue(
            all(
                isinstance(event, chat_schema.ChatStreamMessageEvent)
                for event in events
            )
        )


def _delegation_payload() -> dict[str, object]:
    return {
        "status": "completed",
        "analysis_id": "sales-review",
        "agent_type": "visualizer",
        "session_id": "chart-1",
        "content": "Chart generated",
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
        "failure_reasons": [],
    }


class ChatMessageArtifactTest(unittest.IsolatedAsyncioTestCase):
    def test_large_subagent_tool_payloads_are_preserved(self) -> None:
        call_schema = chat_service._langchain_message_to_schema(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "large-call",
                        "name": "execute_sql",
                        "args": {"sql": "x" * 25_000},
                    }
                ],
            )
        )
        result_schema = chat_service._langchain_message_to_schema(
            ToolMessage(
                content="x" * 55_000,
                name="execute_sql",
                tool_call_id="large-call",
            )
        )

        self.assertIsNotNone(call_schema)
        self.assertIsNotNone(result_schema)
        assert call_schema is not None and result_schema is not None
        call_part = call_schema.parts[-1]
        result_part = result_schema.parts[0]
        self.assertIsInstance(call_part, chat_schema.ToolCallPart)
        self.assertIsInstance(result_part, chat_schema.ToolResultPart)
        assert isinstance(call_part, chat_schema.ToolCallPart)
        assert isinstance(result_part, chat_schema.ToolResultPart)
        self.assertEqual(call_part.args, {"sql": "x" * 25_000})
        self.assertEqual(result_part.content, "x" * 55_000)

    async def test_subagent_custom_stream_is_projected_to_public_events(self) -> None:
        planner = MagicMock()

        async def stream_subagent_activity(
            *args: Any, **kwargs: Any
        ) -> AsyncIterator[dict[str, Any]]:
            del args, kwargs
            yield {
                "type": "custom",
                "ns": (),
                "data": SubagentStatusActivity(
                    delegation_id="delegation-1",
                    analysis_id="sales-review",
                    agent_type="explorer",
                    session_id="source-1",
                    status="running",
                ),
            }
            yield {
                "type": "custom",
                "ns": (),
                "data": SubagentMessageActivity(
                    delegation_id="delegation-1",
                    analysis_id="sales-review",
                    agent_type="explorer",
                    session_id="source-1",
                    message=AIMessage(id="specialist-1", content="正在检查数据"),
                ),
            }
            yield {"type": "custom", "ns": (), "data": {"private": True}}

        planner.astream = stream_subagent_activity
        runtime_mock = MagicMock()
        runtime_mock.planner = planner
        runtime = cast(ConversationAgentRuntime, runtime_mock)
        manager = _TurnManagerStub(
            runtime,
            PlannerTurnContext(
                user_id=7,
                conversation_id=_CONVERSATION_ID,
                max_continuations=0,
            ),
        )
        events: list[chat_schema.ChatStreamEventPayload] = []

        with patch.object(
            chat_service,
            "_schema_to_human_message",
            new=AsyncMock(return_value=HumanMessage(content="analyze")),
        ):
            async for event in chat_service.run_agent_turn(
                manager,
                MagicMock(),
                7,
                _CONVERSATION_ID,
                chat_schema.UserMessageRequest(
                    parts=[chat_schema.TextContent(type="text", text="analyze")]
                ),
                asyncio.Event(),
            ):
                events.append(event)

        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], chat_schema.ChatStreamSubagentStatusEvent)
        self.assertIsInstance(events[1], chat_schema.ChatStreamSubagentMessageEvent)
        message_event = cast(chat_schema.ChatStreamSubagentMessageEvent, events[1])
        self.assertEqual(message_event.delegation_id, "delegation-1")
        self.assertEqual(message_event.message.parts[0].type, "text")

    async def test_semantic_recall_result_is_expanded_in_stream_and_history(
        self,
    ) -> None:
        reference = ToolMessage(
            id="recall-message",
            name="recall_context",
            tool_call_id="recall-call",
            content=json.dumps({"status": "stored", "query": "收入趋势"}),
        )
        detailed_content = json.dumps(
            {
                "query": "收入趋势",
                "tables": {"orders": {"columns": {"amount": {"type": "DECIMAL"}}}},
            },
            ensure_ascii=False,
        )
        expanded = reference.model_copy(update={"content": detailed_content})
        expander = AsyncMock(return_value=[expanded])
        activity = SubagentMessageActivity(
            delegation_id="delegation-1",
            analysis_id="sales-review",
            agent_type="explorer",
            session_id="source-1",
            message=reference,
        )

        with patch.object(
            chat_service,
            "expand_semantic_recall_messages_for_display",
            new=expander,
        ):
            stream_event = await chat_service._subagent_activity_to_event(
                activity,
                7,
                _CONVERSATION_ID,
            )

            runtime = MagicMock()
            runtime.session_service.get_delegation_messages = AsyncMock(
                return_value=[reference]
            )
            agents = MagicMock()
            agents.get_conversation_runtime = AsyncMock(return_value=runtime)
            history = await chat_service.list_subagent_messages(
                agents,
                7,
                _CONVERSATION_ID,
                "sales-review",
                "explorer",
                "source-1",
                "delegation-1",
            )

        self.assertIsInstance(
            stream_event,
            chat_schema.ChatStreamSubagentMessageEvent,
        )
        assert isinstance(stream_event, chat_schema.ChatStreamSubagentMessageEvent)
        stream_part = stream_event.message.parts[0]
        self.assertIsInstance(stream_part, chat_schema.ToolResultPart)
        assert isinstance(stream_part, chat_schema.ToolResultPart)
        self.assertEqual(stream_part.content, detailed_content)
        self.assertIsNotNone(history)
        assert history is not None
        history_part = history[0].parts[0]
        self.assertIsInstance(history_part, chat_schema.ToolResultPart)
        assert isinstance(history_part, chat_schema.ToolResultPart)
        self.assertEqual(history_part.content, detailed_content)
        self.assertEqual(
            reference.content,
            json.dumps({"status": "stored", "query": "收入趋势"}),
        )

    def test_delegation_artifacts_are_restored_from_history(self) -> None:
        message = ToolMessage(
            id="message-1",
            name="delegation",
            tool_call_id="call-1",
            content=json.dumps(_delegation_payload()),
        )

        schema = chat_service._langchain_message_to_schema(message)

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

    async def test_delegation_artifacts_are_in_stream_updates(self) -> None:
        message = ToolMessage(
            id="message-1",
            name="delegation",
            tool_call_id="call-1",
            content=json.dumps(_delegation_payload()),
        )
        planner = MagicMock()

        async def stream_tool_message(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
            yield {
                "type": "updates",
                "ns": (),
                "data": {"tools": {"messages": [message]}},
            }

        planner.astream = stream_tool_message
        runtime_mock = MagicMock()
        runtime_mock.planner = planner
        runtime = cast(ConversationAgentRuntime, runtime_mock)
        turn_context = PlannerTurnContext(
            user_id=7,
            conversation_id=_CONVERSATION_ID,
            max_continuations=0,
        )
        manager = _TurnManagerStub(runtime, turn_context)
        user_message = chat_schema.UserMessageRequest(
            parts=[chat_schema.TextContent(type="text", text="analyze")],
        )
        events: list[chat_schema.ChatStreamEventPayload] = []

        with (
            patch.object(
                chat_service,
                "_schema_to_human_message",
                new=AsyncMock(return_value=HumanMessage(content="analyze")),
            ),
        ):
            async for event in chat_service.run_agent_turn(
                manager,
                MagicMock(),
                7,
                _CONVERSATION_ID,
                user_message,
                asyncio.Event(),
            ):
                events.append(event)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, chat_schema.ChatStreamMessageEvent)
        assert isinstance(event, chat_schema.ChatStreamMessageEvent)
        self.assertEqual(len(event.message.attachments or []), 1)

    def test_invalid_delegation_artifact_payload_is_not_exposed(self) -> None:
        payload = _delegation_payload()
        payload["artifacts"] = [{"path": "/analyses/../secret"}]
        message = ToolMessage(
            id="message-1",
            name="delegation",
            tool_call_id="call-1",
            content=json.dumps(payload),
        )

        schema = chat_service._langchain_message_to_schema(message)

        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertIsNone(schema.attachments)

    def test_noncanonical_delegation_payload_is_not_exposed(self) -> None:
        payload = _delegation_payload()
        payload["artifacts"] = [
            {
                "path": "/analyses/sales-review/sessions/visualizer/chart-1/report.html ",
                "media_type": "text/html",
                "description": "Interactive report",
            }
        ]
        message = ToolMessage(
            id="message-1",
            name="delegation",
            tool_call_id="call-1",
            content=json.dumps(payload),
        )

        schema = chat_service._langchain_message_to_schema(message)

        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertIsNone(schema.attachments)


if __name__ == "__main__":
    unittest.main()
