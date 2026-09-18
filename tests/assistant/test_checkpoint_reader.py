"""使用真实 LangGraph 对照 Assistant Checkpoint 只读投影。"""

from __future__ import annotations

import asyncio
import operator
import unittest
from copy import deepcopy
from typing import Annotated, Any, TypedDict, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from deepagents import create_deep_agent
from deepagents._messages_reducer import _messages_delta_reducer
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph._internal._constants import CONFIG_KEY_CHECKPOINTER
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command, Overwrite, Send, interrupt

from app.assistant.checkpoints.reader import CheckpointStateReader


class _ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class _State(TypedDict, total=False):
    messages: Annotated[
        list, DeltaChannel(cast(Any, _messages_delta_reducer), snapshot_frequency=2)
    ]
    delegation_records: Annotated[dict, operator.or_]


class _Saver(InMemorySaver):
    """在真实 pending writes 落盘后通知测试，避免靠 sleep 安排并发顺序。"""

    def __init__(self):
        super().__init__()
        self.committed: dict[str, asyncio.Event] = {}

    def event(self, message_id: str) -> asyncio.Event:
        return self.committed.setdefault(message_id, asyncio.Event())

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await super().aput_writes(config, writes, task_id, task_path)
        for channel, value in writes:
            if channel == "messages" and isinstance(value, list):
                for message in value:
                    if isinstance(message, AIMessage) and message.id:
                        self.event(message.id).set()


class CheckpointStateReaderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saver = _Saver()
        self.config = RunnableConfig(configurable={"thread_id": str(uuid4())})
        self.reader = CheckpointStateReader(self.saver)

    async def assert_matches(self, graph, *, config=None):
        config = config or self.config
        # 官方投影内部可能修改返回的 checkpoint；两次读取均从 saver 取独立副本。
        expected = await graph.aget_state(config)
        before = await self.saver.aget_tuple(config)
        checkpoint = deepcopy(before.checkpoint) if before else None
        with patch.object(
            self.saver, "aget_delta_channel_history", new_callable=AsyncMock
        ) as delta_history:
            self.assertEqual(
                await self.reader.has_pending_tasks(config), bool(expected.next)
            )
            delta_history.assert_not_awaited()
        actual = await self.reader.read(config)
        self.assertCountEqual(actual.next_nodes, expected.next)
        for channel in ("messages", "delegation_records"):
            self.assertEqual(
                actual.values.get(channel, [] if channel == "messages" else {}),
                expected.values.get(channel, [] if channel == "messages" else {}),
            )
        after = await self.saver.aget_tuple(config)
        self.assertEqual(after.checkpoint if after else None, checkpoint)
        return actual

    async def test_send_tasks_remain_resumable_before_tools_execute(self):
        graph = StateGraph(_State)

        async def model(state):
            return {"messages": [AIMessage(id="model", content="call tools")]}

        async def tools(state):
            return {"messages": [AIMessage(id="tool", content="done")]}

        graph.add_node("model", model)
        graph.add_node("tools", tools)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", lambda state: [Send("tools", state)])
        graph.add_edge("tools", END)
        compiled = graph.compile(checkpointer=self.saver, interrupt_before=["tools"])
        await compiled.ainvoke(
            {"messages": [HumanMessage(id="human", content="hello")]}, self.config
        )
        state = await self.assert_matches(compiled)
        self.assertEqual(state.next_nodes, ("tools",))
        await compiled.ainvoke(None, self.config)
        state = await self.assert_matches(compiled)
        self.assertEqual(state.next_nodes, ())

    async def test_parallel_pending_writes_follow_task_order_and_exclude_completed_tasks(
        self,
    ):
        graph = StateGraph(_State)

        async def a(state):
            await self.saver.event("b").wait()
            return {
                "messages": [AIMessage(id="a", content="a")],
                "delegation_records": {"same": "a"},
            }

        async def b(state):
            return {
                "messages": [AIMessage(id="b", content="b")],
                "delegation_records": {"same": "b"},
            }

        async def c(state):
            await self.saver.event("a").wait()
            raise ValueError("expected failure")

        for name, node in (("a", a), ("b", b), ("c", c)):
            graph.add_node(name, node)
            graph.add_edge(START, name)
            graph.add_edge(name, END)
        compiled = graph.compile(checkpointer=self.saver)
        async with asyncio.timeout(3):
            with self.assertRaisesRegex(ValueError, "expected failure"):
                await compiled.ainvoke(
                    {"messages": [HumanMessage(id="human", content="hello")]},
                    self.config,
                )
        state = await self.assert_matches(compiled)
        self.assertEqual(state.next_nodes, ("c",))
        self.assertEqual(
            [message.id for message in cast(list, state.values["messages"])],
            ["human", "a", "b"],
        )
        self.assertEqual(state.values["delegation_records"], {"same": "b"})

    async def test_parallel_sends_distinguish_completed_and_interrupted_tools(self):
        graph = StateGraph(_State)

        async def tools(state):
            if state["index"] == 1:
                await self.saver.event("tool-0").wait()
                interrupt("approve")
            return {
                "messages": [AIMessage(id=f"tool-{state['index']}", content="done")]
            }

        graph.add_node("tools", tools)
        graph.add_conditional_edges(
            START, lambda state: [Send("tools", {"index": i}) for i in range(2)]
        )
        graph.add_edge("tools", END)
        compiled = graph.compile(checkpointer=self.saver)
        async with asyncio.timeout(3):
            await compiled.ainvoke({"messages": []}, self.config)
        state = await self.assert_matches(compiled)
        self.assertEqual(state.next_nodes, ("tools",))
        self.assertEqual(
            [message.id for message in cast(list, state.values["messages"])], ["tool-0"]
        )
        await compiled.ainvoke(Command(resume=True), self.config)
        state = await self.assert_matches(compiled)
        self.assertEqual(state.next_nodes, ())
        self.assertEqual(
            [message.id for message in cast(list, state.values["messages"])],
            ["tool-0", "tool-1"],
        )

    async def test_unmatched_pending_writes_do_not_enter_business_state(self):
        graph = StateGraph(_State)

        async def node(state):
            return {}

        graph.add_node("node", node)
        graph.add_edge(START, "node")
        graph.add_edge("node", END)
        compiled = graph.compile(checkpointer=self.saver, interrupt_before=["node"])
        await compiled.ainvoke({"messages": []}, self.config)
        saved = await self.saver.aget_tuple(self.config)
        assert saved is not None
        await self.saver.aput_writes(
            saved.config,
            [("messages", [AIMessage(id="stale", content="ignore")])],
            str(uuid4()),
        )
        state = await self.assert_matches(compiled)
        self.assertEqual(state.values["messages"], [])
        self.assertEqual(state.next_nodes, ("node",))

    async def test_delta_snapshots_message_replacement_deletion_and_reset(self):
        graph = StateGraph(_State)

        async def node(state):
            return {}

        graph.add_node("node", node)
        graph.add_edge(START, "node")
        graph.add_edge("node", END)
        compiled = graph.compile(checkpointer=self.saver)
        for messages in (
            [HumanMessage(id="human", content="hello")],
            [AIMessage(id="answer", content="first")],
            [AIMessage(id="answer", content="replaced")],
            [RemoveMessage(id="answer")],
            [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                AIMessage(id="reset", content="reset"),
            ],
        ):
            await compiled.ainvoke({"messages": messages}, self.config)
            await self.assert_matches(compiled)

    async def test_pending_overwrite_matches_framework_channel_semantics(self):
        graph = StateGraph(_State)

        async def a(state):
            return {
                "messages": Overwrite(
                    [AIMessage(id="replacement", content="replace all")]
                )
            }

        async def b(state):
            # 节点 a 的结果已提交后才失败。
            await committed.wait()
            raise ValueError("expected failure")

        committed = asyncio.Event()
        original_put = self.saver.aput_writes

        async def put(config, writes, task_id, task_path=""):
            await original_put(config, writes, task_id, task_path)
            if any(
                isinstance(value, Overwrite)
                for channel, value in writes
                if channel == "messages"
            ):
                committed.set()

        self.saver.aput_writes = put
        for name, node in (("a", a), ("b", b)):
            graph.add_node(name, node)
            graph.add_edge(START, name)
            graph.add_edge(name, END)
        compiled = graph.compile(checkpointer=self.saver)
        async with asyncio.timeout(3):
            with self.assertRaises(ValueError):
                await compiled.ainvoke(
                    {"messages": [HumanMessage(id="human", content="hello")]},
                    self.config,
                )
        state = await self.assert_matches(compiled)
        self.assertEqual(
            [message.id for message in cast(list, state.values["messages"])],
            ["replacement"],
        )

    async def test_nested_namespace_uses_correct_task_ids_and_pending_results(self):
        child = StateGraph(_State)

        async def model(state):
            return {"messages": [AIMessage(id="model", content="call")]}

        async def tools(state):
            return {"messages": [AIMessage(id="tool", content="done")]}

        child.add_node("model", model)
        child.add_node("tools", tools)
        child.add_edge(START, "model")
        child.add_conditional_edges("model", lambda state: [Send("tools", state)])
        child.add_edge("tools", END)
        nested = child.compile(interrupt_before=["tools"])
        parent = StateGraph(_State)
        parent.add_node("specialist", nested)
        parent.add_edge(START, "specialist")
        parent.add_edge("specialist", END)
        compiled = parent.compile(checkpointer=self.saver)
        await compiled.ainvoke({"messages": []}, self.config)
        snapshot = await compiled.aget_state(self.config, subgraphs=True)
        nested_state = snapshot.tasks[0].state
        assert nested_state is not None and not isinstance(nested_state, dict)
        config = RunnableConfig(
            configurable={
                **nested_state.config.get("configurable", {}),
                CONFIG_KEY_CHECKPOINTER: self.saver,
            }
        )
        config.setdefault("configurable", {}).pop("checkpoint_id", None)
        state = await self.assert_matches(nested, config=config)
        self.assertEqual(state.next_nodes, ("tools",))

    async def test_deepagents_actual_middleware_and_tool_route_match(self):
        @tool
        async def ask() -> str:
            """Request approval."""
            return str(interrupt("approve"))

        graph = create_deep_agent(
            model=_ToolModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "ask", "args": {}, "id": "call"}],
                    ),
                    AIMessage(content="done"),
                ]
            ),
            tools=[ask],
            subagents=[],
            checkpointer=self.saver,
        )
        await graph.ainvoke({"messages": [HumanMessage(content="hello")]}, self.config)
        state = await self.assert_matches(graph)
        self.assertEqual(state.next_nodes, ("tools",))
        await graph.ainvoke(Command(resume="approved"), self.config)
        state = await self.assert_matches(graph)
        self.assertEqual(state.next_nodes, ())

    async def test_all_tasks_with_committed_results_are_not_reported_pending(self):
        graph = StateGraph(_State)

        async def node(state):
            return {}

        graph.add_node("node", node)
        graph.add_edge(START, "node")
        graph.add_edge("node", END)
        compiled = graph.compile(checkpointer=self.saver, interrupt_before=["node"])
        await compiled.ainvoke({"messages": []}, self.config)
        expected = await compiled.aget_state(self.config)
        saved = await self.saver.aget_tuple(self.config)
        assert saved is not None
        # 模拟节点结果已落盘、下一份 checkpoint 尚未写入时进程退出。
        await self.saver.aput_writes(
            saved.config,
            [("messages", [AIMessage(id="done", content="done")])],
            expected.tasks[0].id,
        )
        state = await self.assert_matches(compiled)
        self.assertEqual(state.next_nodes, ())
        self.assertEqual(
            [message.id for message in cast(list, state.values["messages"])], ["done"]
        )

    async def test_empty_checkpoint_has_no_pending_tasks(self):
        self.assertFalse(await self.reader.has_pending_tasks(self.config))
        state = await self.reader.read(self.config)
        self.assertEqual(state.values, {})
        self.assertEqual(state.next_nodes, ())
        self.assertIsNone(state.updated_at)
