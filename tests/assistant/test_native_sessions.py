"""验证独立 Session 线程在真实 LangGraph 调用链中的持久化与隔离。"""

import asyncio
import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.assistant.agents.planner.tools.delegation import create_delegation_tool
from app.assistant.agents.specialist_agent import SpecialistAgentState
from app.assistant.execution.manager import AgentManager
from app.assistant.execution.session_service import AgentSessionService
from app.assistant.execution.session_store import PostgresSandboxSessionStore
from app.assistant.execution.types import (
    DelegationRequest,
    DeleteSessionRequest,
    SubagentMessageActivity,
    build_planner_config,
    get_thread_id,
)
from app.shared.contracts.analysis import AgentSessionKey


class NativeSessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conversation_id = uuid4()
        self.saver = InMemorySaver()
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.fail = False

        async def model(state):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            if self.fail:
                raise ValueError("model failed")
            count = sum(isinstance(m, HumanMessage) for m in state["messages"])
            return {
                "messages": [AIMessage(id=uuid4().hex, content=f"child reply {count}")]
            }

        graph = StateGraph(SpecialistAgentState)
        graph.add_node("model", model)
        graph.add_edge(START, "model")
        graph.add_edge("model", END)
        self.agent = graph.compile(checkpointer=self.saver)
        self.persistence = MagicMock()
        self.persistence.list_threads = AsyncMock(
            side_effect=lambda *, prefix: sorted(
                thread
                for thread, namespaces in self.saver.storage.items()
                if thread.startswith(prefix) and any(namespaces.values())
            )
        )
        self.persistence.delete_thread = self.saver.adelete_thread
        self.persistence.get_checkpointer.return_value = self.saver
        self.persistence.advisory_lock.side_effect = lambda *_: asyncio.Lock()
        self.store = PostgresSandboxSessionStore(
            user_id=12,
            conversation_id=self.conversation_id,
            persistence=self.persistence,
            checkpointer=cast(AsyncPostgresSaver, self.saver),
            build_agent=lambda _: self.agent,
            sandbox=MagicMock(delete_session=AsyncMock(return_value=False)),
        )
        self.service = AgentSessionService(
            build_agent=AsyncMock(return_value=self.agent),
            session_store=self.store,
            user_id=12,
            conversation_id=self.conversation_id,
        )
        parent = StateGraph(MessagesState)
        parent.add_node("tools", ToolNode([create_delegation_tool(self.service)]))
        parent.add_edge(START, "tools")
        parent.add_edge("tools", END)
        self.parent = parent.compile(checkpointer=self.saver)
        self.config = build_planner_config(12, self.conversation_id)

    def key(self, session_id="session"):
        return AgentSessionKey(
            12, self.conversation_id, "analysis", "analyst", session_id
        )

    async def delegate(self, call_id, session_id="session"):
        return [
            part
            async for part in self.parent.astream(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "delegation",
                                    "id": call_id,
                                    "args": {
                                        "analysis_id": "analysis",
                                        "agent_type": "analyst",
                                        "session_id": session_id,
                                        "message": "analyze",
                                    },
                                }
                            ],
                        )
                    ]
                },
                self.config,
                stream_mode=["updates", "custom", "messages"],
                version="v2",
            )
        ]

    async def test_thread_continuation_replay_and_parent_stream_isolation(self):
        parts = await self.delegate("first")
        activity = [p["data"] for p in parts if p["type"] == "custom"]
        self.assertTrue(any(isinstance(a, SubagentMessageActivity) for a in activity))
        # 专家 AI 消息只通过 Session 活动展示。
        for part in parts:
            if part["type"] == "messages":
                message = part["data"][0]
                self.assertFalse(
                    isinstance(message, AIMessage)
                    and "child reply" in str(message.content)
                )
        thread = self.key().thread_id
        self.assertEqual(set(self.saver.storage[thread]), {""})
        state = await self.agent.aget_state({"configurable": {"thread_id": thread}})
        self.assertEqual(state.values["messages"][-1].content, "child reply 1")
        self.assertEqual(state.next, ())
        await self.delegate("first")
        self.assertEqual(self.calls, 1)
        await self.delegate("second")
        state = await self.agent.aget_state({"configurable": {"thread_id": thread}})
        self.assertEqual(state.values["messages"][-1].content, "child reply 2")
        await self.delegate("third", "other")
        other = await self.agent.aget_state(
            {"configurable": {"thread_id": self.key("other").thread_id}}
        )
        self.assertEqual(other.values["messages"][-1].content, "child reply 1")
        sessions = await self.service.list_sessions(None)
        self.assertEqual(len(sessions.sessions), 2)

    async def test_parallel_tool_calls_use_separate_threads(self):
        calls = [
            {
                "name": "delegation",
                "id": session_id,
                "args": {
                    "analysis_id": "analysis",
                    "agent_type": "analyst",
                    "session_id": session_id,
                    "message": "analyze",
                },
            }
            for session_id in ("one", "two")
        ]
        result = await self.parent.ainvoke(
            {"messages": [AIMessage(content="", tool_calls=calls)]}, self.config
        )
        self.assertEqual(
            [m.content for m in result["messages"][-2:]],
            ["child reply 1", "child reply 1"],
        )
        self.assertEqual(self.calls, 2)
        for session_id in ("one", "two"):
            self.assertEqual(
                set(self.saver.storage[self.key(session_id).thread_id]), {""}
            )

    async def test_native_delete_isolated_session_and_conversation_cleanup(self):
        await self.delegate("first")
        await self.delegate("second", "other")
        request = DeleteSessionRequest(
            analysis_id="analysis", agent_type="analyst", session_id="session"
        )
        self.assertTrue((await self.service.delete_session(request)).existed)
        self.assertFalse((await self.service.delete_session(request)).existed)
        self.assertIsNone(
            await self.saver.aget_tuple(
                {"configurable": {"thread_id": self.key().thread_id}}
            )
        )
        self.assertIsNotNone(
            await self.saver.aget_tuple(
                {"configurable": {"thread_id": self.key("other").thread_id}}
            )
        )
        await self.delegate("recreated")
        state = await self.agent.aget_state(
            {"configurable": {"thread_id": self.key().thread_id}}
        )
        self.assertEqual(state.values["messages"][-1].content, "child reply 1")
        unrelated = build_planner_config(99, uuid4())
        await self.agent.ainvoke(
            {"messages": [HumanMessage(content="other user")]}, unrelated
        )
        manager = AgentManager(self.persistence, MagicMock(save=AsyncMock()))
        await manager.delete_agent_under_lifecycle_lock(12, self.conversation_id)
        prefix = get_thread_id(12, self.conversation_id)
        self.assertFalse(await self.persistence.list_threads(prefix=prefix))
        self.assertIsNotNone(await self.saver.aget_tuple(unrelated))

    async def test_cancellation_and_failure_persist_terminal_status(self):
        self.release.clear()
        request = DelegationRequest(
            analysis_id="analysis",
            agent_type="analyst",
            session_id="session",
            message="analyze",
        )
        task = asyncio.create_task(
            self.service.execute_delegation(
                request, self.config, delegation_id="cancel"
            )
        )
        await self.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        state = await self.agent.aget_state(
            {"configurable": {"thread_id": self.key().thread_id}}
        )
        self.assertEqual(
            state.values["delegation_records"]["cancel"]["status"], "cancelled"
        )
        self.assertFalse(self.service.is_session_active(self.key().thread_id))
        self.release.set()
        self.fail = True
        result = await self.service.execute_delegation(
            request, self.config, delegation_id="failed"
        )
        self.assertEqual(result.status, "failed")
        replay = await self.service.execute_delegation(
            request, self.config, delegation_id="failed"
        )
        self.assertEqual(
            replay.content, "专业 Agent 执行失败: ValueError: model failed"
        )
        self.assertEqual(self.calls, 2)
