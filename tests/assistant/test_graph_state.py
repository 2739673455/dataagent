"""验证新建图读取 Checkpoint、恢复执行与工具绑定。"""

from unittest.mock import AsyncMock, MagicMock

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from app.assistant.execution import runtime_factory
from app.sandbox.manager import DockerSandboxManager
from app.shared.config import app_config


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        self.seen_tools = tools
        return self

    seen_tools: list = Field(default_factory=list)
    inputs: list = Field(default_factory=list)

    def _generate(self, messages, *args, **kwargs):
        self.inputs.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


register_harness_profile(
    "toolmodel",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
    ),
)


def make_factory(saver):
    persistence = MagicMock()
    persistence.get_checkpointer.return_value = saver
    sandbox = DockerSandboxManager(app_config.cfg.sandbox, MagicMock(), [])
    sandbox.init = AsyncMock(
        side_effect=AssertionError("Docker initialized during read")
    )
    factory = runtime_factory.ConversationAgentRuntimeFactory(
        persistence, sandbox, MagicMock(), MagicMock()
    )
    factory._models = {
        name: ToolModel(responses=[AIMessage(content="unused")])
        for name in factory._model_names
    }
    return factory, persistence, sandbox


import unittest
from uuid import uuid4

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.assistant.execution.manager import AgentManager
from app.assistant.execution.types import (
    SubagentMessageActivity,
    SubagentStatusActivity,
    build_planner_config,
)
from app.query.models.execution import AnalysisQueryResult


class NativeTaskTest(unittest.IsolatedAsyncioTestCase):
    async def test_task_shares_identity_has_fresh_messages_and_no_child_checkpoints(
        self,
    ):
        saver = InMemorySaver()
        factory, _, sandbox = make_factory(saver)
        conversation = uuid4()
        sql_result = AnalysisQueryResult(
            path=f"/data/{conversation}/result.csv", columns=[], row_count=0, sample=[]
        )
        handler = MagicMock(execute=AsyncMock(return_value=sql_result))
        from app.assistant.agents.specialists import build_specialist_definitions
        from app.assistant.agents.tools.execute_sql import create_execute_sql_tool

        factory._definitions = build_specialist_definitions(
            [create_execute_sql_tool(handler)]
        )
        planner = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "first",
                            "name": "task",
                            "args": {
                                "subagent_type": "explorer",
                                "description": "查数",
                            },
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "second",
                            "name": "task",
                            "args": {
                                "subagent_type": "explorer",
                                "description": "重新查数",
                            },
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
        specialist = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "sql",
                            "name": "execute_sql",
                            "args": {"sql": "SELECT 1", "purpose": "统计"},
                        }
                    ],
                ),
                AIMessage(
                    content=f"[[DATAAGENT_ARTIFACT:/data/{conversation}/result.csv]]"
                ),
            ]
        )
        factory._planner_model_name = "planner"
        factory._specialist_model_names = {
            kind: "specialist" for kind in factory._specialist_model_names
        }
        factory._models = {"planner": planner, "specialist": specialist}
        graph = factory.build(12, conversation).planner
        config = build_planner_config(12, conversation)
        chunks = [
            c
            async for c in graph.astream(
                {"messages": [HumanMessage(content="开始")]},
                config,
                stream_mode=["custom", "messages", "updates"],
                version="v2",
            )
        ]
        self.assertEqual(handler.execute.await_count, 2)
        self.assertEqual(
            [
                len([m for m in messages if isinstance(m, HumanMessage)])
                for messages in specialist.inputs
            ],
            [1, 1, 1, 1],
        )
        for call in handler.execute.await_args_list:
            self.assertEqual(call.args, (12, conversation, "SELECT 1"))
        self.assertIn("task", [t.name for t in planner.seen_tools])
        self.assertNotIn("delegation", [t.name for t in planner.seen_tools])
        self.assertNotIn("task", [t.name for t in specialist.seen_tools])
        events = [c["data"] for c in chunks if c["type"] == "custom"]
        self.assertEqual(
            [
                (e.delegation_id, e.status)
                for e in events
                if isinstance(e, SubagentStatusActivity)
            ],
            [
                ("first", "running"),
                ("first", "completed"),
                ("second", "running"),
                ("second", "completed"),
            ],
        )
        self.assertTrue(any(isinstance(e, SubagentMessageActivity) for e in events))
        for checkpoint in saver.list(None):
            self.assertEqual(checkpoint.config["configurable"]["checkpoint_ns"], "")
        state = await graph.aget_state(config)
        results = [m for m in state.values["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual(len(results), 2)
        self.assertTrue(all(m.name == "task" for m in results))
        self.assertTrue(all("result.csv" in str(m.content) for m in results))
        sandbox.init.assert_not_awaited()

    async def test_cold_planner_reads_and_resumes_pending_native_task(self):
        saver = InMemorySaver()
        factory, persistence, _ = make_factory(saver)
        conversation = uuid4()
        planner = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "work",
                            "name": "task",
                            "args": {
                                "subagent_type": "reviewer",
                                "description": "检查",
                            },
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
        factory._planner_model_name = "planner"
        factory._models["planner"] = planner
        graph = factory.build(12, conversation).planner
        config = build_planner_config(12, conversation)
        await graph.ainvoke(
            {"messages": [HumanMessage(content="开始")]},
            config,
            interrupt_before=["tools"],
        )
        cold, _, _ = make_factory(saver)
        manager = AgentManager(
            persistence, MagicMock(exists=AsyncMock(return_value=False)), cold
        )
        self.assertTrue(await manager.can_resume_planner(12, conversation))
        await graph.ainvoke(None, config)
        self.assertFalse(await manager.can_resume_planner(12, conversation))

    async def test_parallel_native_tasks_keep_activity_and_cancellation_isolated(self):
        import asyncio

        from langchain.tools import tool

        from app.assistant.agents.specialists import build_specialist_definitions

        saver = InMemorySaver()
        factory, _, _ = make_factory(saver)
        started = asyncio.Event()
        entered = 0
        cleaned = 0

        @tool
        async def wait_for_cancel() -> str:
            """Wait until the caller cancels."""
            nonlocal entered, cleaned
            entered += 1
            if entered == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned += 1
            return "done"

        factory._definitions = build_specialist_definitions([wait_for_cancel])
        planner = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": id,
                            "name": "task",
                            "args": {"subagent_type": "explorer", "description": id},
                        }
                        for id in ("one", "two")
                    ],
                )
            ]
        )
        specialist = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"id": "wait", "name": "wait_for_cancel", "args": {}}],
                )
            ]
        )
        factory._planner_model_name = "planner"
        factory._specialist_model_names = {
            kind: "specialist" for kind in factory._specialist_model_names
        }
        factory._models = {"planner": planner, "specialist": specialist}
        graph = factory.build(12, uuid4()).planner
        events = []

        async def consume():
            async for chunk in graph.astream(
                {"messages": [HumanMessage(content="开始")]},
                build_planner_config(12, uuid4()),
                stream_mode="custom",
                version="v2",
            ):
                events.append(chunk["data"])

        running = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 3)
        running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await running
        self.assertEqual(cleaned, 2)
        self.assertEqual(
            {
                e.delegation_id
                for e in events
                if isinstance(e, SubagentStatusActivity) and e.status == "running"
            },
            {"one", "two"},
        )
        for checkpoint in saver.list(None):
            self.assertEqual(checkpoint.config["configurable"]["checkpoint_ns"], "")
