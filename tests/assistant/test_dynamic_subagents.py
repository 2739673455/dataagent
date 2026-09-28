"""Dynamic Subagents 协议和 Session 编排单元测试。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections import Counter
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph._internal._constants import (
    CONFIG_KEY_SCRATCHPAD,
    CONFIG_KEY_TASK_ID,
)
from pydantic import Field

from app.assistant.agents.specialists import (
    SpecialistAgentFactory,
    build_specialist_definitions,
)
from app.assistant.execution.manager import AgentManager
from app.assistant.execution.run import ConversationRunService
from app.assistant.execution.session_service import AgentSessionService
from app.assistant.execution.types import (
    DELEGATION_CONTEXT_KEY,
    DelegationMessageContext,
    DelegationRequest,
    DeleteSessionRequest,
    SubagentActivity,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
    build_planner_config,
    conversation_lifecycle_lock_name,
    get_thread_id,
)
from app.shared.contracts.analysis import AGENT_TYPES, AgentSessionKey, AgentType

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
_CONVERSATION_ROOT = f"/data/{_CONVERSATION_ID}"


class RecordingChatModel(BaseChatModel):
    """记录模型请求实际可见的 Tool。"""

    seen_tools: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recording"

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        self.seen_tools = [
            tool_item.get("name", "")
            if isinstance(tool_item, dict)
            else str(getattr(tool_item, "name", ""))
            for tool_item in tools
        ]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="done"))]
        )


@tool
def recall_context(query: str) -> str:
    """检索测试语义资源。"""
    return query


@tool
def execute_sql(sql: str) -> str:
    """执行测试 SQL。"""
    return sql


@tool
def mcp_web_search(query: str) -> str:
    """模拟 MCP 搜索工具。"""
    return query


class _FakeAgent:
    def __init__(
        self,
        *,
        delay: float = 0,
        output: object | None = None,
        stream_messages: list[BaseMessage] | None = None,
        stream_chunks: list[AIMessageChunk] | None = None,
    ) -> None:
        self.delay = delay
        self.output = output
        self.stream_messages = stream_messages or []
        self.stream_chunks = stream_chunks or []
        self.active = 0
        self.max_active = 0
        self.active_by_namespace: Counter[str] = Counter()
        self.max_active_by_namespace: Counter[str] = Counter()
        self.configs: list[RunnableConfig] = []
        self.inputs: list[dict[str, Any]] = []
        self.persisted_sessions: set[str] = set()
        self.workspace_sessions: set[str] = set()
        self.checkpoints: dict[str, dict[str, object]] = {}
        self.state_values: dict[str, dict[str, object]] = {}
        self.state_configs: list[RunnableConfig] = []
        self.checkpointer = object()

    async def ainvoke(
        self,
        input: dict[str, Any],
        config: RunnableConfig,
    ) -> object:
        self.inputs.append(input)
        namespace = str(config.get("configurable", {}).get("thread_id"))
        self.configs.append(config)
        self.active += 1
        self.active_by_namespace[namespace] += 1
        self.max_active = max(self.max_active, self.active)
        self.max_active_by_namespace[namespace] = max(
            self.max_active_by_namespace[namespace],
            self.active_by_namespace[namespace],
        )
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.persisted_sessions.add(namespace)
            self.workspace_sessions.add(namespace)
            existing = self.checkpoints.get(namespace, {}).get("channel_values")
            channel_values = dict(existing) if isinstance(existing, dict) else {}
            messages = [*input.get("messages", []), *self.stream_messages]
            if self.output is not None:
                if isinstance(self.output, dict):
                    messages = self.output.get("messages", messages)
                else:
                    messages.append(AIMessage(content=str(self.output)))
            elif (
                not messages
                or not isinstance(messages[-1], AIMessage)
                or messages[-1].tool_calls
            ):
                messages.append(AIMessage(content="analysis complete"))
            channel_values["messages"] = messages
            records = input.get("delegation_records")
            if isinstance(records, dict):
                channel_values["delegation_records"] = {
                    **(
                        channel_values.get("delegation_records", {})
                        if isinstance(channel_values.get("delegation_records"), dict)
                        else {}
                    ),
                    **records,
                }
            self.checkpoints[namespace] = {
                "ts": "2026-08-29T12:00:00+00:00",
                "channel_values": channel_values,
            }
            return channel_values
        finally:
            self.active_by_namespace[namespace] -= 1
            self.active -= 1

    async def astream(
        self,
        input: dict[str, Any],
        config: RunnableConfig,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any]]:
        """使用 v2 values 事件模拟 CompiledStateGraph 流。"""
        del kwargs
        output = await self.ainvoke(input, config)
        for message in self.stream_chunks:
            yield {
                "type": "messages",
                "ns": (),
                "data": (message, {"langgraph_node": "model"}),
            }
        for message in self.stream_messages:
            node_name = "tools" if isinstance(message, ToolMessage) else "model"
            yield {
                "type": "updates",
                "ns": (),
                "data": {node_name: {"messages": [message]}},
            }
        values = output
        yield {"type": "values", "ns": (), "data": values}

    async def aget_state(self, config: RunnableConfig) -> Any:
        """模拟 CompiledStateGraph 对增量通道完成恢复后的状态读取。"""
        self.state_configs.append(config)
        namespace = str(config.get("configurable", {}).get("thread_id"))
        values = self.state_values.get(namespace)
        if values is None:
            checkpoint = self.checkpoints.get(namespace, {})
            channel_values = checkpoint.get("channel_values")
            values = channel_values if isinstance(channel_values, dict) else {}
        return SimpleNamespace(
            values=values, created_at=self.checkpoints.get(namespace, {}).get("ts")
        )

    async def aupdate_state(
        self,
        config: RunnableConfig,
        values: dict[str, object],
    ) -> None:
        """模拟 CompiledStateGraph 将显式委派状态写回 Checkpoint。"""
        namespace = str(config.get("configurable", {}).get("thread_id"))
        checkpoint = self.checkpoints.setdefault(
            namespace,
            {"ts": "2026-08-29T12:00:00+00:00", "channel_values": {}},
        )
        channels = checkpoint.setdefault("channel_values", {})
        assert isinstance(channels, dict)
        for channel, value in values.items():
            if channel == "delegation_records" and isinstance(value, dict):
                current = channels.get(channel)
                channels[channel] = {
                    **(current if isinstance(current, dict) else {}),
                    **value,
                }
            else:
                channels[channel] = value


def _history_manager(fake: _FakeAgent) -> AgentManager:
    """使用真实生产历史读取链，只替换外部 Checkpointer I/O。"""

    factory = MagicMock()
    factory.specialists.return_value.build.return_value = fake
    return AgentManager(MagicMock(), MagicMock(), factory)


class _DistributedLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[None]:
        lock = self._locks.setdefault(name, asyncio.Lock())
        if lock.locked():
            raise RuntimeError(f"lock busy: {name}")
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def session_lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]:
        return self.acquire(session_key.thread_id)


class _FakeSessionResources:
    """替换 PostgreSQL 和沙箱 I/O，Session 编排使用生产实现。"""

    def __init__(self, fake, lock_factory=None):
        self._fake = fake
        self._lock_factory = lock_factory
        self._session_locks = {}
        self.workspace_delete_failures = 0

    async def list_threads(self, *, prefix):
        return sorted(
            thread
            for thread in self._fake.persisted_sessions
            if thread.startswith(prefix)
        )

    def get_checkpointer(self):
        return self

    async def aget_tuple(self, config):
        thread = config["configurable"]["thread_id"]
        if thread in self._fake.persisted_sessions or thread in self._fake.checkpoints:
            return object()
        return None

    async def adelete_thread(self, thread):
        self._fake.persisted_sessions.discard(thread)
        self._fake.checkpoints.pop(thread, None)
        self._fake.state_values.pop(thread, None)

    async def delete_session(
        self, user_id, conversation_id, analysis_id, agent_type, session_id
    ):
        if self.workspace_delete_failures:
            self.workspace_delete_failures -= 1
            raise RuntimeError("sensitive container failure")
        thread = AgentSessionKey(
            user_id, conversation_id, analysis_id, agent_type, session_id
        ).thread_id
        existed = thread in self._fake.workspace_sessions
        self._fake.workspace_sessions.discard(thread)
        return existed

    @asynccontextmanager
    async def advisory_lock(self, name):
        thread = name.removeprefix("specialist:")
        if self._lock_factory is not None:
            analysis_id, agent_type, session_id = thread.split("/subagents/")[1].split(
                "/"
            )
            key = AgentSessionKey(
                12, _CONVERSATION_ID, analysis_id, agent_type, session_id
            )
            async with self._lock_factory(key):
                yield
            return
        lock = self._session_locks.setdefault(thread, asyncio.Lock())
        if lock.locked():
            raise RuntimeError("Session 正在执行或删除")
        async with lock:
            yield


def _service(
    fake: _FakeAgent,
    *,
    resources: _FakeSessionResources | None = None,
    session_lock_factory: Callable[[AgentSessionKey], AbstractAsyncContextManager[None]]
    | None = None,
) -> AgentSessionService:
    resources = resources or _FakeSessionResources(fake, session_lock_factory)
    return AgentSessionService(
        agents=MagicMock(
            build=MagicMock(return_value=fake), create=AsyncMock(return_value=fake)
        ),
        persistence=cast(Any, resources),
        sandbox=cast(Any, resources),
        user_id=12,
        conversation_id=_CONVERSATION_ID,
    )


def _request(
    session_id: str,
    *,
    agent_type: AgentType = "analyst",
) -> DelegationRequest:
    return DelegationRequest(
        analysis_id="sales-decline",
        agent_type=agent_type,
        session_id=session_id,
        message="analyze the supplied artifact",
    )


class DynamicSubagentContractTest(unittest.TestCase):
    """验证公开协议和专业 Agent 注册约束。"""

    def test_agent_session_key_builds_isolated_namespace(self) -> None:
        key = AgentSessionKey(
            user_id=12,
            conversation_id=uuid4(),
            analysis_id="sales-decline_2026",
            agent_type="analyst",
            session_id="product-category",
        )

        self.assertEqual(
            key.thread_id,
            f"{get_thread_id(12, key.conversation_id)}/subagents/sales-decline_2026/analyst/product-category",
        )

    def test_agent_session_key_rejects_unsafe_identifier(self) -> None:
        identifiers = (
            "",
            "Uppercase",
            "../escape",
            "contains/slash",
            "a" * 65,
            "white space",
        )
        for identifier in identifiers:
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                AgentSessionKey(
                    user_id=12,
                    conversation_id=uuid4(),
                    analysis_id=identifier,
                    agent_type="explorer",
                    session_id="base",
                )

    def test_specialist_agents_expose_shell_and_file_tools(self) -> None:
        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            register_harness_profile,
        )
        from deepagents.backends import LocalShellBackend
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import InMemorySaver

        register_harness_profile(
            "recordingchatmodel",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
            ),
        )
        definitions = build_specialist_definitions([recall_context, execute_sql])
        required_tools = {
            "read_file",
            "write_file",
            "edit_file",
            "shell",
            "view_image",
        }

        with tempfile.TemporaryDirectory() as workspace:
            for agent_type in definitions:
                with self.subTest(agent_type=agent_type):
                    model = RecordingChatModel(
                        profile={
                            "image_inputs": True,
                            "image_tool_message": True,
                        },
                    )
                    shell_backend = LocalShellBackend(root_dir=workspace)
                    cast(Any, shell_backend).workspace_dir = workspace
                    cast(Any, shell_backend).conversation_dir = workspace
                    cast(Any, shell_backend).shell_jobs = shell_backend
                    sandbox = MagicMock()
                    sandbox.get_session_backend = AsyncMock(return_value=shell_backend)
                    factory = SpecialistAgentFactory(
                        definitions,
                        {kind: model for kind in AGENT_TYPES},
                        sandbox,
                        InMemorySaver(),
                        [],
                    )
                    run = asyncio.run(
                        factory.create(
                            AgentSessionKey(
                                user_id=12,
                                conversation_id=_CONVERSATION_ID,
                                analysis_id="test",
                                agent_type=agent_type,
                                session_id="tools",
                            )
                        )
                    )
                    graph = run

                    asyncio.run(
                        graph.ainvoke(
                            {"messages": [HumanMessage(content="inspect tools")]},
                            {"configurable": {"thread_id": agent_type}},
                        )
                    )

                    self.assertTrue(required_tools.issubset(model.seen_tools))
                    self.assertNotIn("task", model.seen_tools)

    def test_planner_exposes_direct_delegation_without_eval(self) -> None:
        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            register_harness_profile,
        )
        from deepagents.backends import LocalShellBackend
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import InMemorySaver

        from app.assistant.agents.planner.agent import create_planner_agent

        @tool
        def delegation(message: str) -> str:
            """委派测试专业 Agent。"""
            return message

        @tool
        def list_sessions() -> str:
            """查询测试专业 Session。"""
            return "[]"

        @tool
        def delete_session(session_id: str) -> str:
            """删除测试专业 Session。"""
            return session_id

        register_harness_profile(
            "recordingchatmodel",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
            ),
        )
        model = RecordingChatModel(
            profile={
                "image_inputs": True,
                "image_tool_message": True,
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            backend = LocalShellBackend(root_dir=workspace)
            cast(Any, backend).shell_jobs = MagicMock()
            cast(Any, backend).conversation_dir = workspace
            graph = create_planner_agent(
                model=model,
                tools=[delegation, list_sessions, delete_session],
                backend=cast(Any, backend),
                checkpointer=InMemorySaver(),
            )

            graph.invoke(
                {"messages": [HumanMessage(content="inspect tools")]},
                {"configurable": {"thread_id": "planner-tools"}},
            )

        self.assertIn("read_file", model.seen_tools)
        self.assertTrue(
            {
                "ls",
                "glob",
                "grep",
                "write_file",
                "edit_file",
                "delete",
                "execute",
            }.isdisjoint(model.seen_tools)
        )
        self.assertTrue(
            {
                "shell",
            }.issubset(model.seen_tools)
        )
        self.assertIn("delegation", model.seen_tools)
        self.assertIn("list_sessions", model.seen_tools)
        self.assertIn("delete_session", model.seen_tools)
        self.assertIn("view_image", model.seen_tools)
        self.assertNotIn("eval", model.seen_tools)


class AgentSessionServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证 Session 并发、故障清理与活动投影。"""

    async def test_list_sessions_reads_persisted_states_and_analysis_filter(
        self,
    ) -> None:
        fake = _FakeAgent()
        completed_ns = f"{get_thread_id(12, _CONVERSATION_ID)}/subagents/sales-decline/analyst/region"
        interrupted_ns = (
            f"{get_thread_id(12, _CONVERSATION_ID)}/subagents/inventory/explorer/base"
        )
        fake.persisted_sessions.update({completed_ns, interrupted_ns})
        completed_context = DelegationMessageContext(
            delegation_id="delegation-completed"
        )
        fake.checkpoints[completed_ns] = {
            "ts": "2026-08-29T12:00:00+00:00",
            "channel_values": {
                "messages": [
                    HumanMessage(
                        content="complete region analysis",
                        additional_kwargs={
                            DELEGATION_CONTEXT_KEY: completed_context.model_dump(
                                mode="json"
                            )
                        },
                    )
                ],
                "delegation_records": {
                    "delegation-completed": {
                        "delegation_id": "delegation-completed",
                        "status": "completed",
                        "result": "region complete",
                    }
                },
            },
        }
        fake.checkpoints[interrupted_ns] = {
            "ts": "2026-08-29T12:01:00+00:00",
            "channel_values": {},
        }
        service = _service(fake)

        all_sessions = await service.list_sessions(None)
        filtered = await service.list_sessions("sales-decline")

        self.assertEqual(
            [session.status for session in all_sessions.sessions],
            ["interrupted", "completed"],
        )
        self.assertEqual(len(filtered.sessions), 1)
        self.assertEqual(filtered.sessions[0].summary, "region complete")

    async def test_list_sessions_reports_active_session_before_checkpoint(
        self,
    ) -> None:
        fake = _FakeAgent(delay=0.05)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        delegation = asyncio.create_task(
            service.execute_delegation(_request("region"), config)
        )
        await asyncio.sleep(0.01)
        listed = await service.list_sessions("sales-decline")
        await delegation

        self.assertEqual(len(listed.sessions), 1)
        self.assertEqual(listed.sessions[0].status, "active")

    async def test_delete_session_retry_finishes_partial_cleanup(self) -> None:
        fake = _FakeAgent()
        store = _FakeSessionResources(fake)
        store.workspace_delete_failures = 1
        service = _service(fake, resources=store)
        config = build_planner_config(12, _CONVERSATION_ID)
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )

        await service.execute_delegation(_request("region"), config)
        with self.assertRaisesRegex(RuntimeError, "删除 Session 工作区失败") as ctx:
            await service.delete_session(request)
        retried = await service.delete_session(request)

        self.assertNotIn("sensitive container failure", str(ctx.exception))
        self.assertTrue(retried.existed)
        self.assertEqual(fake.persisted_sessions, set())
        self.assertEqual(fake.workspace_sessions, set())

    async def test_delete_session_rejects_active_delegation(self) -> None:
        fake = _FakeAgent(delay=0.03)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )

        delegation = asyncio.create_task(
            service.execute_delegation(_request("region"), config)
        )
        await asyncio.sleep(0.005)
        with self.assertRaisesRegex(RuntimeError, "Session 正在执行或删除"):
            await service.delete_session(request)
        delegation_result = await delegation

        self.assertEqual(delegation_result.status, "completed")
        self.assertIn(request.session_id, " ".join(fake.persisted_sessions))

    async def test_same_session_conflict_fails_while_other_sessions_run_parallel(
        self,
    ) -> None:
        fake = _FakeAgent(delay=0.03)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        results = await asyncio.gather(
            service.execute_delegation(_request("region"), config),
            service.execute_delegation(_request("region"), config),
            service.execute_delegation(_request("product"), config),
        )

        self.assertEqual(
            [result.status for result in results].count("completed"),
            2,
        )
        failed = next(result for result in results if result.status == "failed")
        self.assertIn("Session 正在执行或删除", failed.content)
        region_ns = f"{get_thread_id(12, _CONVERSATION_ID)}/subagents/sales-decline/analyst/region"
        self.assertEqual(fake.max_active_by_namespace[region_ns], 1)
        self.assertGreaterEqual(fake.max_active, 2)

    async def test_same_session_conflict_fails_across_service_instances(self) -> None:
        fake = _FakeAgent(delay=0.03)
        distributed_locks = _DistributedLockRegistry()
        first_service = _service(
            fake,
            session_lock_factory=distributed_locks.session_lock,
        )
        second_service = _service(
            fake,
            session_lock_factory=distributed_locks.session_lock,
        )
        first_config = build_planner_config(12, _CONVERSATION_ID)
        second_config = build_planner_config(12, _CONVERSATION_ID)
        results = await asyncio.gather(
            first_service.execute_delegation(_request("region"), first_config),
            second_service.execute_delegation(_request("region"), second_config),
        )

        namespace = f"{get_thread_id(12, _CONVERSATION_ID)}/subagents/sales-decline/analyst/region"
        self.assertEqual(fake.max_active_by_namespace[namespace], 1)
        self.assertEqual(
            [result.status for result in results].count("failed"),
            1,
        )

    async def test_delegation_builds_controlled_subagent_config(self) -> None:
        fake = _FakeAgent()
        service = _service(fake)
        parent = build_planner_config(12, _CONVERSATION_ID)
        parent["metadata"] = {"trace": "kept"}
        parent_configurable = parent.setdefault("configurable", {})
        parent_configurable["checkpoint_id"] = "planner-checkpoint"
        parent_configurable[CONFIG_KEY_TASK_ID] = "planner-tool-task"
        parent_configurable[CONFIG_KEY_SCRATCHPAD] = object()
        result = await service.execute_delegation(_request("region"), parent)

        self.assertEqual(result.status, "completed")
        invoked = fake.configs[0]
        self.assertEqual(invoked.get("metadata"), {"trace": "kept"})
        invoked_configurable = invoked.get("configurable", {})
        parent_configurable = parent.get("configurable", {})
        self.assertEqual(
            invoked_configurable.get("thread_id"),
            f"{get_thread_id(12, _CONVERSATION_ID)}/subagents/sales-decline/analyst/region",
        )
        self.assertNotEqual(
            invoked_configurable.get("thread_id"),
            parent_configurable.get("thread_id"),
        )
        self.assertNotIn("checkpoint_ns", invoked_configurable)
        self.assertNotIn("checkpoint_id", invoked_configurable)
        self.assertNotIn(CONFIG_KEY_TASK_ID, invoked_configurable)
        self.assertNotIn(CONFIG_KEY_SCRATCHPAD, invoked_configurable)

    async def test_plain_final_answer_is_returned_without_extra_model_calls(
        self,
    ) -> None:
        delegation_id = "delegation-current-answer"
        current_answer = "有使用 skill：analysis 与 visualization。"
        fake = _FakeAgent(
            output={
                "messages": [
                    HumanMessage(
                        content="先前分析",
                        additional_kwargs={
                            DELEGATION_CONTEXT_KEY: {
                                "delegation_id": "delegation-previous",
                            }
                        },
                    ),
                    AIMessage(content="旧的分析交付摘要"),
                    HumanMessage(
                        content="是否使用 skill？",
                        additional_kwargs={
                            DELEGATION_CONTEXT_KEY: {
                                "delegation_id": delegation_id,
                            }
                        },
                    ),
                    AIMessage(content=current_answer),
                ]
            }
        )
        service = _service(fake)

        with patch(
            "app.assistant.execution.session_service.uuid4",
            return_value=SimpleNamespace(hex=delegation_id),
        ):
            result = await service.execute_delegation(
                _request("region"),
                build_planner_config(12, _CONVERSATION_ID),
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, current_answer)
        self.assertEqual(len(fake.configs), 1)

    async def test_empty_text_fails_without_retry(self):
        fake = _FakeAgent(output={"messages": []})
        result = await _service(fake).execute_delegation(
            _request("region"), build_planner_config(12, _CONVERSATION_ID)
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("未返回最终文本", result.content)
        self.assertEqual(len(fake.inputs), 1)

    async def test_text_attachment_directive_is_returned_unchanged(self):
        text = f"分析完成\n[[DATAAGENT_ARTIFACT:{_CONVERSATION_ROOT}/sessions/sales-decline/analyst/region/report.html]]"
        fake = _FakeAgent(output=text)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        result = await service.execute_delegation(
            _request("region"), config, delegation_id="text-result"
        )
        replay = await service.execute_delegation(
            _request("region"), config, delegation_id="text-result"
        )
        self.assertEqual(result.content, text)
        self.assertEqual(replay.content, text)
        self.assertEqual(len(fake.inputs), 1)

    async def test_delegation_streams_public_messages_and_statuses(self) -> None:
        tool_call = AIMessage(
            id="specialist-tool-call",
            content="正在查询区域销售数据",
            tool_calls=[
                {
                    "id": "sql-call",
                    "name": "execute_sql",
                    "args": {"sql": "select region, sum(gmv) from sales"},
                }
            ],
        )
        tool_result = ToolMessage(
            id="specialist-tool-result",
            content="华东,1200",
            name="execute_sql",
            tool_call_id="sql-call",
        )
        final_response = AIMessage(
            id="final-response",
            content="analysis complete",
        )
        fake = _FakeAgent(
            stream_messages=[
                tool_call,
                tool_call,
                tool_result,
                final_response,
            ],
        )
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        activities: list[SubagentActivity] = []

        result = await service.execute_delegation(
            _request("region"),
            config,
            delegation_id="delegation-region",
            activity_writer=activities.append,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            [
                activity.status
                for activity in activities
                if isinstance(activity, SubagentStatusActivity)
            ],
            ["running", "completed"],
        )
        messages = [
            activity
            for activity in activities
            if isinstance(activity, SubagentMessageActivity)
        ]
        self.assertEqual(
            [activity.message.id for activity in messages],
            ["specialist-tool-call", "specialist-tool-result", "final-response"],
        )
        self.assertTrue(
            all(activity.delegation_id == "delegation-region" for activity in messages)
        )
        task_message = cast(HumanMessage, fake.inputs[0]["messages"][0])
        self.assertEqual(
            task_message.additional_kwargs[DELEGATION_CONTEXT_KEY],
            {"delegation_id": "delegation-region"},
        )

    async def test_delegation_emits_incremental_reasoning_activity(self) -> None:
        fake = _FakeAgent(
            stream_chunks=[
                AIMessageChunk(
                    id="specialist-answer",
                    content=[{"type": "reasoning", "reasoning": "先检查"}],
                ),
                AIMessageChunk(
                    id="specialist-answer",
                    content=[{"type": "reasoning", "reasoning": "表结构"}],
                ),
                AIMessageChunk(id="specialist-answer", content="开始查询"),
            ]
        )
        service = _service(fake)
        activities: list[SubagentActivity] = []

        await service.execute_delegation(
            _request("region"),
            build_planner_config(12, _CONVERSATION_ID),
            delegation_id="delegation-region",
            activity_writer=activities.append,
        )

        thinking = [
            activity
            for activity in activities
            if isinstance(activity, SubagentThinkingDeltaActivity)
        ]
        self.assertEqual(
            [activity.delta for activity in thinking], ["先检查", "表结构"]
        )
        self.assertEqual([activity.reset for activity in thinking], [True, False])
        self.assertTrue(
            all(activity.message_id == "specialist-answer" for activity in thinking)
        )
        message_deltas = [
            activity
            for activity in activities
            if isinstance(activity, SubagentMessageDeltaActivity)
        ]
        self.assertEqual([activity.delta for activity in message_deltas], ["开始查询"])
        self.assertTrue(message_deltas[0].reset)

    async def test_history_uses_active_sessions_without_building_a_runtime(
        self,
    ) -> None:
        fake = _FakeAgent()
        namespace = f"{get_thread_id(12, _CONVERSATION_ID)}/subagents/sales-decline/analyst/region"
        fake.state_values[namespace] = {
            "messages": [
                HumanMessage(
                    content="work",
                    additional_kwargs={
                        DELEGATION_CONTEXT_KEY: {"delegation_id": "running-work"}
                    },
                )
            ],
            "delegation_records": {
                "running-work": {"delegation_id": "running-work", "status": "running"}
            },
        }
        manager = _history_manager(fake)
        runtime = MagicMock()
        runtime.session_service.is_session_active.return_value = True
        manager._active_sessions[(12, _CONVERSATION_ID)] = runtime.session_service
        with patch.object(
            manager._runtime_factory, "create", new_callable=AsyncMock
        ) as create:
            active = await manager.read_delegation_activity(
                12,
                _CONVERSATION_ID,
                "sales-decline",
                "analyst",
                "region",
                "running-work",
            )
            assert active is not None
            self.assertEqual(active.status, "running")
            runtime.session_service.is_session_active.assert_called_once_with(namespace)
            manager._active_sessions.clear()
            interrupted = await manager.read_delegation_activity(
                12,
                _CONVERSATION_ID,
                "sales-decline",
                "analyst",
                "region",
                "running-work",
            )
            assert interrupted is not None
            self.assertEqual(interrupted.status, "cancelled")
            create.assert_not_awaited()

    async def test_read_delegation_activity_segments_checkpoint_history(self) -> None:
        fake = _FakeAgent()
        namespace = f"{get_thread_id(12, _CONVERSATION_ID)}/subagents/sales-decline/analyst/region"
        first_context = DelegationMessageContext(delegation_id="delegation-first")
        second_context = DelegationMessageContext(delegation_id="delegation-second")
        first_ai = AIMessage(id="first-ai", content="第一轮分析")
        first_tool = ToolMessage(
            id="first-tool",
            content="第一轮结果",
            name="execute_sql",
            tool_call_id="first-call",
        )
        second_ai = AIMessage(id="second-ai", content="第二轮分析")
        fake.checkpoints[namespace] = {
            "ts": "2026-08-29T12:00:00+00:00",
            "channel_values": {},
        }
        fake.state_values[namespace] = {
            "delegation_records": {
                "delegation-first": {
                    "delegation_id": "delegation-first",
                    "status": "completed",
                    "result": "第一轮完成",
                }
            },
            "messages": [
                HumanMessage(
                    content="first",
                    additional_kwargs={
                        DELEGATION_CONTEXT_KEY: first_context.model_dump(mode="json")
                    },
                ),
                first_ai,
                first_tool,
                AIMessage(
                    id="final-response",
                    content="第一轮完成",
                ),
                HumanMessage(
                    content="second",
                    additional_kwargs={
                        DELEGATION_CONTEXT_KEY: second_context.model_dump(mode="json")
                    },
                ),
                second_ai,
            ],
        }
        manager = _history_manager(fake)

        activity = await manager.read_delegation_activity(
            12,
            _CONVERSATION_ID,
            "sales-decline",
            "analyst",
            "region",
            "delegation-first",
        )
        missing = await manager.read_delegation_activity(
            12,
            _CONVERSATION_ID,
            "sales-decline",
            "analyst",
            "region",
            "delegation-missing",
        )

        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertEqual(
            activity.messages,
            [
                first_ai,
                first_tool,
                AIMessage(id="final-response", content="第一轮完成"),
            ],
        )
        self.assertEqual(activity.status, "completed")
        self.assertIsNone(missing)
        self.assertEqual(len(fake.state_configs), 2)

    async def test_read_delegation_activity_keeps_unfinished_older_run_cancelled(
        self,
    ) -> None:
        fake = _FakeAgent()
        namespace = f"{get_thread_id(12, _CONVERSATION_ID)}/subagents/sales-decline/analyst/region"
        first_context = DelegationMessageContext(delegation_id="delegation-first")
        second_context = DelegationMessageContext(delegation_id="delegation-second")
        fake.state_values[namespace] = {
            "messages": [
                HumanMessage(
                    content="first",
                    additional_kwargs={
                        DELEGATION_CONTEXT_KEY: first_context.model_dump(mode="json")
                    },
                ),
                AIMessage(id="first-ai", content="尚未完成"),
                HumanMessage(
                    content="second",
                    additional_kwargs={
                        DELEGATION_CONTEXT_KEY: second_context.model_dump(mode="json")
                    },
                ),
            ]
        }
        manager = _history_manager(fake)

        activity = await manager.read_delegation_activity(
            12,
            _CONVERSATION_ID,
            "sales-decline",
            "analyst",
            "region",
            "delegation-first",
        )

        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertEqual(
            activity.messages, [AIMessage(id="first-ai", content="尚未完成")]
        )
        self.assertEqual(activity.status, "cancelled")

    async def test_delegation_conflict_emits_failed_status(self) -> None:
        fake = _FakeAgent(delay=0.05)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        activities: list[SubagentActivity] = []

        active = asyncio.create_task(
            service.execute_delegation(_request("region"), config)
        )
        await asyncio.sleep(0.005)
        result = await service.execute_delegation(
            _request("region"),
            config,
            delegation_id="delegation-conflict",
            activity_writer=activities.append,
        )
        await active

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            [
                activity.status
                for activity in activities
                if isinstance(activity, SubagentStatusActivity)
            ],
            ["failed"],
        )

    async def test_delegation_cancellation_emits_cancelled_status(self) -> None:
        fake = _FakeAgent(delay=0.2)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        activities: list[SubagentActivity] = []

        task = asyncio.create_task(
            service.execute_delegation(
                _request("region"),
                config,
                delegation_id="delegation-cancel",
                activity_writer=activities.append,
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(
            [
                activity.status
                for activity in activities
                if isinstance(activity, SubagentStatusActivity)
            ],
            ["running", "cancelled"],
        )
        channels = fake.checkpoints[
            f"{get_thread_id(12, _CONVERSATION_ID)}/subagents/sales-decline/analyst/region"
        ]["channel_values"]
        assert isinstance(channels, dict)
        records = channels["delegation_records"]
        assert isinstance(records, dict)
        record = records["delegation-cancel"]
        assert isinstance(record, dict)
        self.assertEqual(record["status"], "cancelled")

    async def test_run_rejects_same_planner_across_workers(self) -> None:
        locks = _DistributedLockRegistry()
        provider = MagicMock(advisory_lock=locks.acquire)
        release = asyncio.Event()

        @asynccontextmanager
        async def use_runtime(*args):
            yield runtime

        async def stream(**kwargs):
            await release.wait()
            if False:
                yield {}

        runtime = MagicMock()
        runtime.planner.astream = stream
        first = ConversationRunService(
            MagicMock(use_runtime=use_runtime), MagicMock(), provider
        )
        second = ConversationRunService(
            MagicMock(use_runtime=use_runtime), MagicMock(), provider
        )
        try:
            events = await first.start(12, _CONVERSATION_ID, None, prepare=AsyncMock())
            with self.assertRaises(RuntimeError):
                await second.start(12, _CONVERSATION_ID, None, prepare=AsyncMock())
            self.assertTrue(await first.is_running(12, _CONVERSATION_ID))
            release.set()
            self.assertEqual([e.type async for e in events], ["done"])
        finally:
            await first.close()
            await second.close()

    async def test_persisted_tombstone_blocks_other_worker_execution(self) -> None:
        distributed_locks = _DistributedLockRegistry()
        tombstone = False

        tombstones = MagicMock()

        async def write_tombstone(*args: object, **kwargs: object) -> None:
            nonlocal tombstone
            del args, kwargs
            tombstone = True

        tombstones.save = AsyncMock(side_effect=write_tombstone)
        tombstones.exists = AsyncMock(side_effect=lambda *_: tombstone)
        persistence = MagicMock()
        persistence.delete_thread = AsyncMock()
        persistence.list_threads = AsyncMock(return_value=[])
        persistence.advisory_lock = lambda *args, **kwargs: distributed_locks.acquire(
            "conversation"
        )
        deleting_worker = AgentManager(persistence, tombstones, MagicMock())
        serving_worker = AgentManager(MagicMock(), tombstones, MagicMock())

        async with persistence.advisory_lock(
            conversation_lifecycle_lock_name(12, _CONVERSATION_ID)
        ):
            await deleting_worker.delete_agent_under_lifecycle_lock(
                12, _CONVERSATION_ID
            )

        with self.assertRaisesRegex(RuntimeError, "已被删除"):
            async with serving_worker.use_runtime(12, _CONVERSATION_ID):
                self.fail("deleted conversation entered execution")
        persistence.delete_thread.assert_awaited_once()
