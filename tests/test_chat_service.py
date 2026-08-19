"""聊天回合与 Planner 自动续写测试"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from app.agents.contracts import PlannerTurnContext
from app.agents.manager import ConversationAgentRuntime
from app.routes.api.v1.chat import schemas as chat_schema
from app.services import chat_service

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


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
    ) -> AsyncIterator[dict[str, Any]]:
        self.configs.append(config)
        self.input_sizes.append(len(input["messages"]))
        yield {
            "model": {
                "messages": [
                    AIMessage(
                        id=f"response-{len(self.configs)}",
                        content="partial response",
                        response_metadata={"finish_reason": self.finish_reason},
                    )
                ]
            }
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
            planner_run_id=f"{finish_reason}-turn",
            max_continuations=2,
        )
        manager = _TurnManagerStub(runtime, turn_context)

        user_message = chat_schema.MessageSchema(
            message_id="user-message",
            role="user",
            parts=[chat_schema.TextContent(text="analyze")],
        )
        responses: list[chat_schema.MessageSchema] = []
        with (
            patch.object(chat_service, "agent_manager", manager),
            patch.object(
                chat_service.message_mapper,
                "schema_to_human_message",
                new=AsyncMock(return_value=HumanMessage(content="analyze")),
            ),
            self.assertRaisesRegex(
                chat_service.PlannerContinuationLimitError,
                "exceeded 2 continuations",
            ),
        ):
            async for response in chat_service.run_agent_turn(
                7,
                _CONVERSATION_ID,
                user_message,
                asyncio.Event(),
            ):
                responses.append(response)

        self.assertEqual(manager.execution_count, 1)
        self.assertEqual(len(planner.configs), 3)
        self.assertEqual(
            {
                config.get("configurable", {}).get("planner_run_id")
                for config in planner.configs
            },
            {turn_context.planner_run_id},
        )
        self.assertEqual(planner.input_sizes, [1, 0, 0])
        self.assertEqual(len(responses), 3)


if __name__ == "__main__":
    unittest.main()
