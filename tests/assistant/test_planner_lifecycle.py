"""Planner 流提前退出时，图资源必须先于执行锁释放。"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from app.assistant.execution import planner
from app.assistant.execution.types import PlannerTurnContext


@pytest.mark.parametrize("exit_mode", ["close", "projection_error"])
def test_graph_stream_closes_before_execution_context_exits(exit_mode) -> None:
    order = []
    conversation_id = uuid4()

    async def graph_stream(**kwargs):
        try:
            yield {
                "type": "messages",
                "data": (AIMessageChunk(id="answer", content="开始"), {}),
            }
        finally:
            await asyncio.sleep(0)
            order.append("graph closed")

    @asynccontextmanager
    async def execution(*args, **kwargs):
        try:
            yield runtime
        finally:
            order.append("execution released")

    runtime = MagicMock()
    runtime.planner.astream = graph_stream
    manager = MagicMock(use_runtime=execution)

    async def run():
        events = planner.run_agent_turn(
            manager,
            MagicMock(),
            PlannerTurnContext(1, conversation_id, 0),
            None,
        )
        if exit_mode == "projection_error":
            with (
                patch.object(
                    planner.MessageDeltaParser,
                    "parse",
                    side_effect=ValueError("invalid message"),
                ),
                pytest.raises(ValueError, match="invalid message"),
            ):
                await anext(events)
        else:
            assert (await anext(events)).type == "message_delta"
            assert order == []
            await events.aclose()
        assert order == ["graph closed", "execution released"]

    asyncio.run(run())
