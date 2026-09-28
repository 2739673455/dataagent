"""真实执行图与冷启动图共用原生 Checkpoint 读取。"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from app.assistant.execution import runtime_factory
from app.assistant.execution.manager import AgentManager
from app.assistant.execution.types import build_planner_config
from app.sandbox.manager import DockerSandboxManager
from app.shared.config import app_config
from app.shared.contracts.analysis import AgentSessionKey


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        self.seen_tools = tools
        return self

    seen_tools: list = Field(default_factory=list)


def make_factory(saver):
    persistence = MagicMock()
    persistence.get_checkpointer.return_value = saver
    persistence.list_threads = AsyncMock(return_value=[])
    sandbox = DockerSandboxManager(app_config.cfg.sandbox, MagicMock(), [])
    sandbox.init = AsyncMock(
        side_effect=AssertionError("Docker initialized during read")
    )
    factory = runtime_factory.ConversationAgentRuntimeFactory(
        persistence, sandbox, MagicMock(), MagicMock()
    )
    return factory, persistence, sandbox


class GraphStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_cold_read_restores_pending_tools_without_initializing_resources(
        self,
    ):
        saver = InMemorySaver()
        factory, persistence, sandbox = make_factory(saver)
        conversation = uuid4()
        config = build_planner_config(12, conversation)
        model = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"id": "list", "name": "list_sessions", "args": {}}],
                ),
                AIMessage(content="finished"),
            ]
        )
        factory._models = {name: model for name in factory._model_names}
        graph = factory.build(12, conversation).planner
        # list_sessions 不读沙箱；真实模型/工具路由先停在工具节点。
        await graph.ainvoke(
            {"messages": [HumanMessage(content="sessions")]},
            config,
            interrupt_before=["tools"],
        )
        assert "task" not in [t.name for t in model.seen_tools]
        expected = await graph.aget_state(config)
        cold, _, _ = make_factory(saver)
        manager = AgentManager(
            persistence, MagicMock(exists=AsyncMock(return_value=False)), cold
        )
        with (
            patch.object(
                runtime_factory,
                "create_configured_model",
                side_effect=AssertionError("model initialized"),
            ),
            patch.object(
                runtime_factory,
                "get_mcp_tools",
                side_effect=AssertionError("MCP connected"),
            ),
        ):
            actual = await manager.read_planner_state(12, conversation)
            assert actual.values == expected.values
            assert actual.next == ("tools",)
            assert await manager.can_resume_planner(12, conversation)
        await graph.ainvoke(None, config)
        assert not (await manager.read_planner_state(12, conversation)).next
        assert (await manager.read_planner_state(12, conversation)).values["messages"][
            -1
        ].content == "finished"
        sandbox.init.assert_not_awaited()

    async def test_specialist_cold_graph_preserves_messages_and_delegation_records(
        self,
    ):
        saver = InMemorySaver()
        factory, _, _ = make_factory(saver)
        key = AgentSessionKey(12, uuid4(), "sales", "reviewer", "check")
        config = {"configurable": {"thread_id": key.thread_id}}
        model = ToolModel(responses=[AIMessage(content="reviewed")])
        factory._models = {name: model for name in factory._model_names}
        graph = factory.specialists().build(key)
        await graph.ainvoke(
            {
                "messages": [HumanMessage(content="review")],
                "delegation_records": {"one": {"status": "running"}},
            },
            config,
        )
        await graph.aupdate_state(
            config,
            {
                "delegation_records": {
                    "one": {"status": "completed", "result": "reviewed"}
                }
            },
        )
        cold, _, sandbox = make_factory(saver)
        state = await cold.specialists().build(key).aget_state(config)
        assert state.values["messages"][-1].content == "reviewed"
        assert state.values["delegation_records"]["one"]["status"] == "completed"
        assert state.next == ()
        sandbox.init.assert_not_awaited()

    async def test_mcp_tools_resolve_at_execution_and_survive_resume(self):
        calls = []

        @tool
        async def lookup(value: str) -> str:
            """Return a lookup result."""
            calls.append(value)
            return f"found {value}"

        saver = InMemorySaver()
        factory, _, _ = make_factory(saver)
        key = AgentSessionKey(12, uuid4(), "sales", "explorer", "lookup")
        config = {"configurable": {"thread_id": key.thread_id}}
        model = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "mcp", "name": "lookup", "args": {"value": "sales"}}
                    ],
                ),
                AIMessage(content="finished"),
            ]
        )
        factory._models = {name: model for name in factory._model_names}
        graph = factory.specialists().build(key)
        factory._mcp_tools = [lookup]
        await graph.ainvoke(
            {"messages": [HumanMessage(content="lookup")]},
            config,
            interrupt_before=["tools"],
        )
        assert "lookup" in [t.name for t in model.seen_tools]
        assert "task" not in [t.name for t in model.seen_tools]
        cold, _, _ = make_factory(saver)
        state = await cold.specialists().build(key).aget_state(config)
        assert state.next == ("tools",)
        await graph.ainvoke(None, config)
        assert calls == ["sales"]
        state = await graph.aget_state(config)
        assert any(
            isinstance(m, ToolMessage) and m.content == "found sales"
            for m in state.values["messages"]
        )

    async def test_cold_state_includes_completed_parallel_tool_writes(self):
        committed = asyncio.Event()
        release = asyncio.Event()
        count = 0

        class Saver(InMemorySaver):
            async def aput_writes(self, config, writes, task_id, task_path=""):
                await super().aput_writes(config, writes, task_id, task_path)
                if any(
                    channel == "messages"
                    and isinstance(value, list)
                    and any(
                        isinstance(m, ToolMessage) and m.tool_call_id == "good"
                        for m in value
                    )
                    for channel, value in writes
                ):
                    committed.set()

        @tool
        async def good() -> str:
            """Complete a lookup."""
            nonlocal count
            count += 1
            return "completed lookup"

        @tool
        async def blocked() -> str:
            """Wait for another lookup."""
            await release.wait()
            return "released"

        saver = Saver()
        factory, _, _ = make_factory(saver)
        key = AgentSessionKey(12, uuid4(), "sales", "explorer", "parallel")
        config = {"configurable": {"thread_id": key.thread_id}}
        model = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "good", "name": "good", "args": {}},
                        {"id": "blocked", "name": "blocked", "args": {}},
                    ],
                ),
                AIMessage(content="finished"),
            ]
        )
        factory._models = {name: model for name in factory._model_names}
        factory._mcp_tools = [good, blocked]
        graph = factory.specialists().build(key)
        task = asyncio.create_task(
            graph.ainvoke({"messages": [HumanMessage(content="lookups")]}, config)
        )
        try:
            await asyncio.wait_for(committed.wait(), 3)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        cold, _, _ = make_factory(saver)
        actual = await cold.specialists().build(key).aget_state(config)
        expected = await graph.aget_state(config)
        assert actual.values == expected.values
        assert actual.next == ("tools",)
        assert any(
            isinstance(m, ToolMessage) and m.tool_call_id == "good"
            for m in actual.values["messages"]
        )
        release.set()
        await graph.ainvoke(None, config)
        assert count == 1
