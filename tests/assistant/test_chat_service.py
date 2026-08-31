"""聊天回合与 Planner 自动续写测试。"""

from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from app.assistant.agents.contracts import (
    EVAL_DELEGATIONS_KEY,
    MESSAGE_CREATED_AT_KEY,
    ConversationAgentRuntime,
    DelegationActivityHistory,
    PlannerTurnContext,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
)
from app.assistant.agents.middleware.message_timestamp import (
    MessageTimestampMiddleware,
)
from app.assistant.agents.middleware.user_message_attachments import (
    USER_MESSAGE_ATTACHMENTS_KEY,
    UserMessageAttachments,
)
from app.assistant.agents.middleware.user_message_metadata import (
    USER_MESSAGE_METADATA_KEY,
    UserMessageMetadata,
)
from app.assistant.api.chat import schemas as chat_schema
from app.assistant.services import chat as chat_service
from app.sandbox.paths import normalize_attachment_path

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


class _FileInspectorStub:
    """按用户、会话和路径返回可下载状态。"""

    def __init__(self, available: set[tuple[int, UUID, str]] | None = None) -> None:
        self.available = available or set()
        self.calls: list[tuple[int, UUID, str]] = []

    async def is_downloadable_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bool:
        call = (user_id, conversation_id, path)
        self.calls.append(call)
        return call in self.available


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


class ChatMutationRequestTest(unittest.TestCase):
    def test_delete_conversations_requires_at_least_one_id(self) -> None:
        with self.assertRaises(ValidationError):
            chat_schema.DeleteConversationRequest(conversation_ids=[])

    def test_delete_attachment_requires_a_path(self) -> None:
        with self.assertRaises(ValidationError):
            chat_schema.DeleteAttachmentRequest(
                conversation_id=_CONVERSATION_ID,
                f_path="",
            )


class MessageTimestampTest(unittest.IsolatedAsyncioTestCase):
    async def test_user_message_creation_time_is_persisted(self) -> None:
        message = chat_service._schema_to_human_message(
            chat_schema.UserMessageRequest(
                parts=[chat_schema.TextContent(type="text", text="analyze")]
            ),
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
        message = chat_service._schema_to_human_message(
            chat_schema.UserMessageRequest(
                parts=[chat_schema.TextContent(type="text", text="analyze")]
            ),
        )

        response = chat_service._langchain_message_to_schema(message)

        self.assertIsNotNone(response)
        assert response is not None
        payload = response.model_dump(mode="json")
        self.assertNotIn(USER_MESSAGE_METADATA_KEY, payload)
        self.assertIsNotNone(payload["created_at"])
        self.assertNotIn("<message_metadata>", json.dumps(payload))

    async def test_attachment_references_are_private_and_content_stays_raw(
        self,
    ) -> None:
        message = chat_service._schema_to_human_message(
            chat_schema.UserMessageRequest(
                parts=[chat_schema.TextContent(type="text", text="analyze")],
                attachments=[
                    chat_schema.AttachmentReference(f_path="uploads/report.csv"),
                    chat_schema.AttachmentReference(f_path="uploads/chart.png"),
                ],
            )
        )

        self.assertEqual(message.content, [{"type": "text", "text": "analyze"}])
        private_attachments = UserMessageAttachments.model_validate(
            message.additional_kwargs[USER_MESSAGE_ATTACHMENTS_KEY]
        )
        self.assertEqual(
            [item.f_path for item in private_attachments.attachments],
            ["uploads/report.csv", "uploads/chart.png"],
        )
        response = chat_service._langchain_message_to_schema(message)
        assert response is not None
        self.assertEqual(
            [item.f_path for item in response.attachments or ()],
            ["uploads/report.csv", "uploads/chart.png"],
        )
        self.assertNotIn(
            USER_MESSAGE_ATTACHMENTS_KEY,
            response.model_dump(mode="json"),
        )

    async def test_model_response_creation_time_is_persisted(self) -> None:
        middleware = MessageTimestampMiddleware()
        response_message = AIMessage(content="result")

        async def handler(_: Any) -> ModelResponse[Any]:
            return ModelResponse(result=[response_message])

        await middleware.awrap_model_call(MagicMock(), handler)

        self.assertIn(MESSAGE_CREATED_AT_KEY, response_message.additional_kwargs)

    async def test_reasoning_content_is_projected_as_completed_thinking(self) -> None:
        response = chat_service._langchain_message_to_schema(
            AIMessage(
                id="answer-1",
                content="最终回答",
                additional_kwargs={"reasoning_content": "先核对数据，再回答。"},
            )
        )

        assert response is not None
        self.assertEqual(
            response.parts,
            [
                chat_schema.ThinkingContent(
                    type="thinking",
                    text="先核对数据，再回答。",
                    status="complete",
                ),
                chat_schema.TextContent(type="text", text="最终回答"),
            ],
        )


class _RepeatingPlanner:
    """重复返回同一 finish reason 并记录 Planner 配置。"""

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
    """记录一个聊天回合进入的执行上下文次数。"""

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
    async def test_planner_reasoning_streams_incrementally_then_is_completed(
        self,
    ) -> None:
        planner = MagicMock()

        async def stream_reasoning(
            *args: Any, **kwargs: Any
        ) -> AsyncIterator[dict[str, Any]]:
            del args, kwargs
            for delta in ("先定位", "数据源"):
                yield {
                    "type": "messages",
                    "ns": (),
                    "data": (
                        AIMessageChunk(
                            id="answer-1",
                            content="",
                            additional_kwargs={"reasoning_content": delta},
                        ),
                        {"langgraph_node": "model"},
                    ),
                }
            for delta in ("完", "成"):
                yield {
                    "type": "messages",
                    "ns": (),
                    "data": (
                        AIMessageChunk(
                            id="answer-1",
                            content=delta,
                        ),
                        {"langgraph_node": "model"},
                    ),
                }
            yield {
                "type": "updates",
                "ns": (),
                "data": {
                    "model": {
                        "messages": [
                            AIMessage(
                                id="answer-1",
                                content="完成",
                                additional_kwargs={"reasoning_content": "先定位数据源"},
                                response_metadata={"finish_reason": "stop"},
                            )
                        ]
                    }
                },
            }

        planner.astream = stream_reasoning
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

        events = [
            event
            async for event in chat_service.resume_agent_turn(
                manager,
                _FileInspectorStub(),
                7,
                _CONVERSATION_ID,
                asyncio.Event(),
            )
        ]

        self.assertEqual(
            [type(event) for event in events],
            [
                chat_schema.ChatStreamThinkingEvent,
                chat_schema.ChatStreamThinkingEvent,
                chat_schema.ChatStreamMessageDeltaEvent,
                chat_schema.ChatStreamMessageDeltaEvent,
                chat_schema.ChatStreamMessageEvent,
            ],
        )
        first = cast(chat_schema.ChatStreamThinkingEvent, events[0])
        second = cast(chat_schema.ChatStreamThinkingEvent, events[1])
        first_text = cast(chat_schema.ChatStreamMessageDeltaEvent, events[2])
        second_text = cast(chat_schema.ChatStreamMessageDeltaEvent, events[3])
        final = cast(chat_schema.ChatStreamMessageEvent, events[4])
        self.assertTrue(first.reset)
        self.assertFalse(second.reset)
        self.assertEqual(first.delta + second.delta, "先定位数据源")
        self.assertTrue(first_text.reset)
        self.assertFalse(second_text.reset)
        self.assertEqual(first_text.delta + second_text.delta, "完成")
        self.assertEqual(final.message.parts[0].type, "thinking")

    async def test_resume_uses_pending_checkpoint_without_new_human_message(
        self,
    ) -> None:
        planner = MagicMock()
        received_inputs: list[object] = []

        async def resume_stream(
            input: object,
            config: RunnableConfig,
            **kwargs: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            del config, kwargs
            received_inputs.append(input)
            yield {
                "type": "updates",
                "ns": (),
                "data": {
                    "model": {
                        "messages": [
                            AIMessage(
                                id="resumed-final",
                                content="恢复后的最终回答",
                                response_metadata={"finish_reason": "stop"},
                            )
                        ]
                    }
                },
            }

        planner.astream = resume_stream
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

        events = [
            event
            async for event in chat_service.resume_agent_turn(
                manager,
                _FileInspectorStub(),
                7,
                _CONVERSATION_ID,
                asyncio.Event(),
            )
        ]

        self.assertEqual(received_inputs, [None])
        self.assertEqual(len(events), 1)
        event = cast(chat_schema.ChatStreamMessageEvent, events[0])
        part = event.message.parts[0]
        self.assertIsInstance(part, chat_schema.TextContent)
        assert isinstance(part, chat_schema.TextContent)
        self.assertEqual(part.text, "恢复后的最终回答")

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
                new=MagicMock(return_value=HumanMessage(content="analyze")),
            ),
            self.assertRaisesRegex(
                chat_service.PlannerContinuationLimitError,
                "连续续写次数超过上限",
            ),
        ):
            async for event in chat_service.run_agent_turn(
                manager,
                _FileInspectorStub(),
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
        "agent_type": "analyst",
        "session_id": "chart-1",
        "content": "Chart generated",
        "artifacts": [
            {
                "path": ("/sessions/sales-review/analyst/chart-1/report.html"),
                "media_type": "text/html",
                "description": "Interactive report",
            }
        ],
        "repair_requests": [],
        "failure_reasons": [],
    }


class ChatMessageArtifactTest(unittest.IsolatedAsyncioTestCase):
    async def test_final_artifact_directives_project_downloadable_session_files(
        self,
    ) -> None:
        report_path = "/sessions/sales-review/analyst/chart-1/report.html"
        missing_path = "/sessions/sales-review/analyst/chart-1/missing.csv"
        code_path = "/sessions/sales-review/analyst/chart-1/example.png"
        message = AIMessage(
            id="assistant-final",
            content=(
                "报告已生成。\n"
                f"[[DATAAGENT_ARTIFACT:{report_path}]]\n"
                f"[[DATAAGENT_ARTIFACT:{report_path}]]\n"
                "```text\n"
                f"[[DATAAGENT_ARTIFACT:{code_path}]]\n"
                "```\n"
                f"    [[DATAAGENT_ARTIFACT:{code_path}]]\n"
                f"[[DATAAGENT_ARTIFACT:{missing_path}]]"
            ),
            response_metadata={"finish_reason": "stop"},
        )
        files = _FileInspectorStub(
            {(7, _CONVERSATION_ID, report_path.removeprefix("/"))}
        )

        schema = await chat_service._langchain_message_to_schema_with_artifacts(
            message,
            files,
            7,
            _CONVERSATION_ID,
        )

        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertEqual(len(schema.attachments or ()), 1)
        attachment = (schema.attachments or [])[0]
        self.assertEqual(attachment.f_path, report_path.removeprefix("/"))
        self.assertEqual(attachment.media_type, "text/html")
        rendered_text = "".join(
            part.text
            for part in schema.parts
            if isinstance(part, chat_schema.TextContent)
        )
        self.assertNotIn(
            f"[[DATAAGENT_ARTIFACT:{report_path}]]",
            rendered_text,
        )
        self.assertIn(f"[[DATAAGENT_ARTIFACT:{code_path}]]", rendered_text)
        self.assertIn(
            f"    [[DATAAGENT_ARTIFACT:{code_path}]]",
            rendered_text,
        )
        self.assertIn(f"[[DATAAGENT_ARTIFACT:{missing_path}]]", rendered_text)
        self.assertEqual(
            files.calls,
            [
                (7, _CONVERSATION_ID, report_path.removeprefix("/")),
                (7, _CONVERSATION_ID, missing_path.removeprefix("/")),
            ],
        )

    async def test_artifact_directives_only_apply_to_final_assistant_messages(
        self,
    ) -> None:
        path = "/sessions/sales-review/explorer/source-1/result.csv"
        message = AIMessage(
            content=f"[[DATAAGENT_ARTIFACT:{path}]]",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "delegation",
                    "args": {},
                }
            ],
            response_metadata={"finish_reason": "tool_calls"},
        )
        files = _FileInspectorStub({(7, _CONVERSATION_ID, path.removeprefix("/"))})

        schema = await chat_service._langchain_message_to_schema_with_artifacts(
            message,
            files,
            7,
            _CONVERSATION_ID,
        )

        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertIsNone(schema.attachments)
        self.assertEqual(files.calls, [])
        text_part = schema.parts[0]
        self.assertIsInstance(text_part, chat_schema.TextContent)
        assert isinstance(text_part, chat_schema.TextContent)
        self.assertEqual(text_part.text, f"[[DATAAGENT_ARTIFACT:{path}]]")

    async def test_artifact_directive_file_check_is_scoped_to_conversation(
        self,
    ) -> None:
        path = "/sessions/sales-review/analyst/main/final.csv"
        other_conversation_id = UUID("660e8400-e29b-41d4-a716-446655440000")
        message = AIMessage(
            content=f"[[DATAAGENT_ARTIFACT:{path}]]",
            response_metadata={"finish_reason": "stop"},
        )
        files = _FileInspectorStub({(7, other_conversation_id, path.removeprefix("/"))})

        schema = await chat_service._langchain_message_to_schema_with_artifacts(
            message,
            files,
            7,
            _CONVERSATION_ID,
        )

        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertIsNone(schema.attachments)
        self.assertEqual(
            files.calls,
            [(7, _CONVERSATION_ID, path.removeprefix("/"))],
        )
        text_part = schema.parts[0]
        self.assertIsInstance(text_part, chat_schema.TextContent)
        assert isinstance(text_part, chat_schema.TextContent)
        self.assertEqual(text_part.text, f"[[DATAAGENT_ARTIFACT:{path}]]")

    async def test_history_and_live_stream_use_same_artifact_projection(self) -> None:
        path = "/sessions/sales-review/analyst/main/final.csv"
        message = AIMessage(
            id="assistant-final",
            content=f"结果见附件。\n[[DATAAGENT_ARTIFACT:{path}]]",
            response_metadata={"finish_reason": "stop"},
        )
        planner = MagicMock()
        planner.aget_state = AsyncMock(
            return_value=SimpleNamespace(values={"messages": [message]})
        )

        async def stream_final_message(
            *args: Any,
            **kwargs: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            del args, kwargs
            yield {
                "type": "updates",
                "ns": (),
                "data": {"model": {"messages": [message]}},
            }

        planner.astream = stream_final_message
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
        files = _FileInspectorStub({(7, _CONVERSATION_ID, path.removeprefix("/"))})

        history = await chat_service.list_messages(
            manager,
            files,
            7,
            _CONVERSATION_ID,
        )
        events = [
            event
            async for event in chat_service.run_agent_turn(
                manager,
                files,
                7,
                _CONVERSATION_ID,
                chat_schema.UserMessageRequest(
                    parts=[chat_schema.TextContent(type="text", text="分析")]
                ),
                asyncio.Event(),
            )
        ]

        self.assertEqual(len(history), 1)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, chat_schema.ChatStreamMessageEvent)
        assert isinstance(event, chat_schema.ChatStreamMessageEvent)
        self.assertEqual(
            history[0].model_dump(mode="json"),
            event.message.model_dump(mode="json"),
        )

    def test_eval_internal_delegations_are_projected_from_tool_metadata(self) -> None:
        payload = _delegation_payload()
        schema = chat_service._langchain_message_to_schema(
            ToolMessage(
                id="eval-result",
                content="done",
                name="eval",
                tool_call_id="eval-call",
                additional_kwargs={
                    EVAL_DELEGATIONS_KEY: [
                        {
                            "delegation_id": "ptc-delegation-1",
                            "analysis_id": "sales-review",
                            "agent_type": "analyst",
                            "session_id": "chart-1",
                            "message": "生成销售图表",
                            "result": payload,
                        }
                    ]
                },
            )
        )

        self.assertIsNotNone(schema)
        assert schema is not None and schema.eval_delegations is not None
        self.assertEqual(schema.eval_delegations[0].delegation_id, "ptc-delegation-1")
        self.assertEqual(schema.eval_delegations[0].message, "生成销售图表")
        self.assertEqual(schema.eval_delegations[0].result, payload)

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
                    parent_tool_call_id="eval-call",
                    instruction="定位销售数据",
                ),
            }
            yield {
                "type": "custom",
                "ns": (),
                "data": SubagentThinkingDeltaActivity(
                    delegation_id="delegation-1",
                    analysis_id="sales-review",
                    agent_type="explorer",
                    session_id="source-1",
                    message_id="specialist-1",
                    delta="先检查数据",
                    reset=True,
                    parent_tool_call_id="eval-call",
                    instruction="定位销售数据",
                ),
            }
            yield {
                "type": "custom",
                "ns": (),
                "data": SubagentMessageDeltaActivity(
                    delegation_id="delegation-1",
                    analysis_id="sales-review",
                    agent_type="explorer",
                    session_id="source-1",
                    message_id="specialist-1",
                    delta="正在检查数据",
                    reset=True,
                    parent_tool_call_id="eval-call",
                    instruction="定位销售数据",
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
            new=MagicMock(return_value=HumanMessage(content="analyze")),
        ):
            async for event in chat_service.run_agent_turn(
                manager,
                _FileInspectorStub(),
                7,
                _CONVERSATION_ID,
                chat_schema.UserMessageRequest(
                    parts=[chat_schema.TextContent(type="text", text="analyze")]
                ),
                asyncio.Event(),
            ):
                events.append(event)

        self.assertEqual(len(events), 4)
        self.assertIsInstance(events[0], chat_schema.ChatStreamSubagentStatusEvent)
        self.assertIsInstance(events[1], chat_schema.ChatStreamSubagentThinkingEvent)
        self.assertIsInstance(
            events[2], chat_schema.ChatStreamSubagentMessageDeltaEvent
        )
        self.assertIsInstance(events[3], chat_schema.ChatStreamSubagentMessageEvent)
        thinking_event = cast(chat_schema.ChatStreamSubagentThinkingEvent, events[1])
        self.assertEqual(thinking_event.delta, "先检查数据")
        self.assertTrue(thinking_event.reset)
        delta_event = cast(chat_schema.ChatStreamSubagentMessageDeltaEvent, events[2])
        self.assertEqual(delta_event.delta, "正在检查数据")
        message_event = cast(chat_schema.ChatStreamSubagentMessageEvent, events[3])
        self.assertEqual(message_event.delegation_id, "delegation-1")
        self.assertEqual(message_event.message.parts[0].type, "text")
        status_event = cast(chat_schema.ChatStreamSubagentStatusEvent, events[0])
        self.assertEqual(status_event.parent_tool_call_id, "eval-call")
        self.assertEqual(status_event.instruction, "定位销售数据")

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
            runtime.session_service.get_delegation_activity = AsyncMock(
                return_value=DelegationActivityHistory(
                    messages=[reference],
                    status="completed",
                )
            )
            agents = MagicMock()
            agents.get_conversation_runtime = AsyncMock(return_value=runtime)
            history = await chat_service.get_subagent_activity(
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
        self.assertEqual(history.status, "completed")
        history_part = history.messages[0].parts[0]
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
            "sessions/sales-review/analyst/chart-1/report.html",
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
                new=MagicMock(return_value=HumanMessage(content="analyze")),
            ),
        ):
            async for event in chat_service.run_agent_turn(
                manager,
                _FileInspectorStub(),
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
        payload["artifacts"] = [{"path": "/sessions/../secret"}]
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
                "path": "/sessions/sales-review/analyst/chart-1/report.html ",
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
