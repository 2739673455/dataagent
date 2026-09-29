"""Planner 流提前退出或转换失败时关闭底层图流。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from app.assistant.services import run as run_service
from app.assistant.services.types import PlannerTurnContext


@pytest.mark.parametrize("exit_mode", ["close", "projection_error"])
def test_graph_stream_closes_on_exit_or_projection_error(exit_mode) -> None:
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

    graph = MagicMock(astream=graph_stream)
    manager = MagicMock(create_planner=AsyncMock(return_value=graph))

    async def run():
        events = run_service.run_agent_turn(
            manager,
            MagicMock(),
            PlannerTurnContext(1, conversation_id, 0),
            None,
        )
        if exit_mode == "projection_error":
            with (
                patch.object(
                    run_service.MessageDeltaParser,
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
        assert order == ["graph closed"]

    asyncio.run(run())
