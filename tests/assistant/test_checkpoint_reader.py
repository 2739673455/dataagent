"""Assistant Checkpoint 只读投影测试。"""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    CheckpointTuple,
    empty_checkpoint,
)

from app.assistant.agents.checkpoint_reader import CheckpointStateReader


class CheckpointStateReaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_replays_delta_channel_messages_and_current_pending_write(
        self,
    ) -> None:
        config = RunnableConfig(
            configurable={
                "thread_id": "thread-1",
                "checkpoint_ns": "",
                "checkpoint_id": "checkpoint-2",
            }
        )
        checkpoint = empty_checkpoint()
        checkpoint["channel_versions"] = {"messages": "2"}
        saved = CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata={},
            pending_writes=[
                (
                    "task-2",
                    "messages",
                    [AIMessage(id="assistant-1", content="answer")],
                )
            ],
        )
        checkpointer = MagicMock(spec=BaseCheckpointSaver)
        checkpointer.aget_tuple = AsyncMock(return_value=saved)
        checkpointer.aget_delta_channel_history = AsyncMock(
            return_value={
                "messages": {
                    "writes": [
                        (
                            "task-1",
                            "messages",
                            [HumanMessage(id="user-1", content="question")],
                        )
                    ]
                }
            }
        )

        state = await CheckpointStateReader(
            cast(BaseCheckpointSaver[Any], checkpointer)
        ).read(config)

        messages = cast(list[Any], state.values["messages"])
        self.assertEqual(
            [message.id for message in messages], ["user-1", "assistant-1"]
        )
        checkpointer.aget_delta_channel_history.assert_awaited_once_with(
            config=config,
            channels=["messages"],
        )


if __name__ == "__main__":
    unittest.main()
