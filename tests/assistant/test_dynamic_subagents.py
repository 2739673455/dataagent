"""Dynamic Subagents 协议和 Session 编排单元测试。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections import Counter
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
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
from langgraph.checkpoint.base import CheckpointTuple, empty_checkpoint
from langgraph.constants import CONFIG_KEY_CHECKPOINTER
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field, ValidationError

from app.assistant.agents import runtime as runtime_factory
from app.assistant.agents.filesystem import agent_skills_mount_path
from app.assistant.contracts import (
    DELEGATION_CONTEXT_KEY,
    DelegationMessageContext,
    DelegationRequest,
    DeleteSessionRequest,
)
from app.assistant.execution.activity import SessionActivity
from app.assistant.execution.delegation import (
    DelegationExecutor,
    SpecialistAgentRun,
)
from app.assistant.execution.events import (
    SubagentActivity,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
)
from app.assistant.execution.runs import ConversationRunService
from app.assistant.execution.runtime_cache import AgentManager
from app.assistant.execution.shell_jobs import ShellJobRuntime
from app.assistant.models.session import AgentSessionKey
from app.assistant.repositories.checkpoint_reader import CheckpointState
from app.assistant.repositories.session import SessionCheckpointRepository
from app.assistant.sessions.identity import (
    build_planner_config,
    session_checkpoint_namespace,
)
from app.assistant.sessions.management import AgentSessionService
from app.assistant.sessions.state_reader import AgentStateReader
from app.sandbox.contracts import SandboxSessionScope
from app.shared.config.app_config import OrchestrationConfig
from app.shared.contracts.analysis import AGENT_TYPES, AgentType

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
_CONVERSATION_ROOT = f"/data/{_CONVERSATION_ID}"


class RecordingChatModel(BaseChatModel):
    """记录模型请求实际可见的 Tool。"""

    seen_tools: list[str] = Field(default_factory=list)
    seen_tool_choice: str | None = None
    seen_bind_kwargs: dict[str, Any] = Field(default_factory=dict)
    response_content: str = "done"
    response_contents: list[str] = Field(default_factory=list)

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
        self.seen_tool_choice = tool_choice
        self.seen_bind_kwargs = kwargs
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
        content = (
            self.response_contents.pop(0)
            if self.response_contents
            else self.response_content
        )
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))]
        )


@tool
def mcp_web_search(query: str) -> str:
    """模拟 MCP 搜索工具。"""
    return query


async def _create_runtime(
    models: dict[AgentType, BaseChatModel],
    sandbox: Any,
    checkpointer: Any,
) -> runtime_factory.ConversationAgentRuntimeFactory:
    """用本地模型和 MCP 替身初始化真实运行时。"""

    @asynccontextmanager
    async def model_context(name: str) -> AsyncGenerator[BaseChatModel]:
        yield models[cast(AgentType, name)]

    factory = runtime_factory.ConversationAgentRuntimeFactory(
        persistence=MagicMock(checkpointer=checkpointer),
        locks=MagicMock(),
        sandbox=sandbox,
        recall=MagicMock(),
        query=MagicMock(),
        activity=SessionActivity(),
    )
    config = SimpleNamespace(
        lm_config=SimpleNamespace(active="explorer"),
        agent=SimpleNamespace(
            specialists={kind: SimpleNamespace(model=kind) for kind in AGENT_TYPES}
        ),
    )
    with (
        patch.object(runtime_factory.app_config, "cfg", config),
        patch.object(runtime_factory, "create_configured_model", model_context),
        patch.object(
            runtime_factory, "get_mcp_tools", AsyncMock(return_value=[mcp_web_search])
        ),
    ):
        await factory.init()
    return factory


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
        namespace = str(config.get("configurable", {}).get("checkpoint_ns"))
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
            if self.output is not None:
                output = self.output
            else:
                configurable = config.get("configurable", {})
                artifact_path = f"{configurable['workspace_dir']}/result.json"
                output = {
                    "messages": self.stream_messages
                    or [
                        AIMessage(
                            content=f"analysis complete\n[[DATAAGENT_ARTIFACT:{artifact_path}]]"
                        )
                    ]
                }
            existing = self.checkpoints.get(namespace, {}).get("channel_values")
            channel_values = dict(existing) if isinstance(existing, dict) else {}
            messages = output.get("messages", []) if isinstance(output, dict) else []
            if not any(
                DELEGATION_CONTEXT_KEY in message.additional_kwargs
                for message in messages
            ):
                messages = [
                    *channel_values.get("messages", []),
                    *input.get("messages", []),
                    *messages,
                ]
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
        values = output if isinstance(output, dict) else {}
        yield {"type": "values", "ns": (), "data": values}

    async def aget_state(self, config: RunnableConfig) -> Any:
        """模拟 CompiledStateGraph 对增量通道完成恢复后的状态读取。"""
        self.state_configs.append(config)
        namespace = str(config.get("configurable", {}).get("checkpoint_ns"))
        values = self.state_values.get(namespace)
        if values is None:
            checkpoint = self.checkpoints.get(namespace, {})
            channel_values = checkpoint.get("channel_values")
            values = channel_values if isinstance(channel_values, dict) else {}
        return SimpleNamespace(values=values)

    async def aupdate_state(
        self,
        config: RunnableConfig,
        values: dict[str, object],
    ) -> None:
        """模拟 CompiledStateGraph 将显式委派状态写回 Checkpoint。"""
        namespace = str(config.get("configurable", {}).get("checkpoint_ns"))
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


def _history_manager(fake: _FakeAgent) -> AgentStateReader:
    """使用真实生产历史读取链，只替换外部 Checkpointer I/O。"""

    async def get_tuple(config: RunnableConfig):
        namespace = str(config.get("configurable", {}).get("checkpoint_ns"))
        checkpoint = empty_checkpoint()
        checkpoint.update(cast(Any, fake.checkpoints.get(namespace, {})))
        checkpoint["channel_values"] = fake.state_values.get(
            namespace, checkpoint.get("channel_values", {})
        )
        checkpoint["channel_values"].setdefault("messages", [])
        return CheckpointTuple(
            checkpoint=checkpoint,
            config=config,
            metadata={"step": 0},
            pending_writes=[],
        )

    persistence = MagicMock()
    persistence.checkpointer.aget_tuple = AsyncMock(side_effect=get_tuple)
    return AgentStateReader(
        persistence, MagicMock(exists=AsyncMock(return_value=False)), SessionActivity()
    )


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
        return self.acquire(session_checkpoint_namespace(session_key))


class _FakeSessionStore(SessionCheckpointRepository):
    def __init__(
        self,
        fake: _FakeAgent,
        *,
        lock_factory: Callable[
            [AgentSessionKey],
            AbstractAsyncContextManager[None],
        ]
        | None = None,
    ) -> None:
        self._fake = fake
        self._lock_factory = lock_factory
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._reserved_session_namespaces: set[str] = set()
        self.workspace_delete_failures = 0

    async def list_namespaces(self, analysis_id: str | None) -> list[str]:
        prefix = f"subagents/{analysis_id}/" if analysis_id else "subagents/"
        return sorted(
            namespace
            for namespace in self._fake.persisted_sessions
            if namespace.startswith(prefix)
        )

    async def read_state(
        self,
        session_key: AgentSessionKey,
    ) -> CheckpointState:
        namespace = session_checkpoint_namespace(session_key)
        values = self._fake.state_values.get(namespace)
        checkpoint = self._fake.checkpoints.get(namespace)
        if values is None and checkpoint is not None:
            raw_values = checkpoint.get("channel_values")
            values = raw_values if isinstance(raw_values, dict) else {}
        return CheckpointState(
            values=values or {},
            next_nodes=(),
            updated_at=(
                datetime.fromisoformat(str(checkpoint.get("ts")))
                if checkpoint is not None
                else None
            ),
        )

    async def delete_checkpoint(self, session_key: AgentSessionKey) -> bool:
        namespace = session_checkpoint_namespace(session_key)
        existed = (
            namespace in self._fake.persisted_sessions
            or namespace in self._fake.checkpoints
        )
        self._fake.persisted_sessions.discard(namespace)
        self._fake.checkpoints.pop(namespace, None)
        return existed

    async def delete_workspace(self, session_key: AgentSessionKey) -> bool:
        if self.workspace_delete_failures:
            self.workspace_delete_failures -= 1
            raise RuntimeError("sensitive container failure")
        namespace = session_checkpoint_namespace(session_key)
        existed = namespace in self._fake.workspace_sessions
        self._fake.workspace_sessions.discard(namespace)
        return existed

    def lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]:
        if self._lock_factory is not None:
            return self._lock_factory(session_key)
        return self._local_session_lock(session_key)

    @asynccontextmanager
    async def _local_session_lock(
        self,
        session_key: AgentSessionKey,
    ) -> AsyncGenerator[None]:
        lock = self._session_locks.setdefault(
            session_checkpoint_namespace(session_key), asyncio.Lock()
        )
        if lock.locked():
            raise RuntimeError("Session 正在执行或删除")
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()

    @asynccontextmanager
    async def reserve_capacity(
        self,
        session_key: AgentSessionKey,
        max_sessions: int,
    ) -> AsyncGenerator[None]:
        namespace = session_checkpoint_namespace(session_key)
        if namespace in self._fake.persisted_sessions:
            yield
            return
        occupied = self._fake.persisted_sessions | self._reserved_session_namespaces
        if namespace not in occupied and len(occupied) >= max_sessions:
            raise RuntimeError("当前 Conversation 的 Session 数量已达上限")
        self._reserved_session_namespaces.add(namespace)
        try:
            yield
        finally:
            self._reserved_session_namespaces.discard(namespace)


def _service(
    fake: _FakeAgent,
    *,
    max_parallel_sessions: int = 8,
    max_sessions: int = 128,
    session_store: _FakeSessionStore | None = None,
    session_lock_factory: Callable[
        [AgentSessionKey],
        AbstractAsyncContextManager[None],
    ]
    | None = None,
) -> SimpleNamespace:
    graph = cast(CompiledStateGraph, fake)

    async def build_agent(session_key: AgentSessionKey) -> SpecialistAgentRun:
        del session_key
        shell_jobs = MagicMock()
        shell_jobs.cleanup = AsyncMock()
        return SpecialistAgentRun(
            agent=graph,
            shell_jobs=cast(Any, shell_jobs),
        )

    store = session_store or _FakeSessionStore(fake, lock_factory=session_lock_factory)
    activity = SessionActivity()

    async def delete_workspace(user_id, conversation_id, scope: SandboxSessionScope):
        return await store.delete_workspace(
            AgentSessionKey(
                user_id,
                conversation_id,
                scope.analysis_id,
                cast(AgentType, scope.agent_type),
                scope.session_id,
            )
        )

    sessions = AgentSessionService(
        session_store=store,
        control=cast(Any, store),
        activity=activity,
        sandbox=cast(Any, SimpleNamespace(delete_session=delete_workspace)),
        user_id=12,
        conversation_id=_CONVERSATION_ID,
    )
    executor = DelegationExecutor(
        build_agent=build_agent,
        session_store=store,
        control=cast(Any, store),
        activity=activity,
        user_id=12,
        conversation_id=_CONVERSATION_ID,
        max_parallel_sessions=max_parallel_sessions,
        max_sessions=max_sessions,
    )
    return SimpleNamespace(sessions=sessions, executor=executor)


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

    def test_orchestration_limits_are_validated_when_loading_config(self):
        limits = {"max_parallel_sessions": 1, "max_sessions": 1, "max_continuations": 0}
        self.assertEqual(OrchestrationConfig(**limits).max_continuations, 0)
        for name, value in (
            ("max_parallel_sessions", 0),
            ("max_parallel_sessions", -1),
            ("max_sessions", 0),
            ("max_sessions", -1),
            ("max_continuations", -1),
        ):
            with (
                self.subTest(field=name, value=value),
                self.assertRaises(ValidationError),
            ):
                OrchestrationConfig(**{**limits, name: value})

    def test_agent_session_key_builds_isolated_namespace(self) -> None:
        key = AgentSessionKey(
            user_id=12,
            conversation_id=uuid4(),
            analysis_id="sales-decline_2026",
            agent_type="analyst",
            session_id="product-category",
        )

        self.assertEqual(
            session_checkpoint_namespace(key),
            "subagents/sales-decline_2026/analyst/product-category",
        )

    def test_session_requests_reject_unsafe_identifiers(self) -> None:
        identifiers = (
            "",
            "Uppercase",
            "../escape",
            "contains/slash",
            "a" * 65,
            "white space",
        )
        for schema in (DelegationRequest, DeleteSessionRequest):
            for name in ("analysis_id", "session_id"):
                for identifier in identifiers:
                    fields = {
                        "analysis_id": "sales",
                        "agent_type": "explorer",
                        "session_id": "base",
                        name: identifier,
                    }
                    if schema is DelegationRequest:
                        fields["message"] = "query data"
                    with (
                        self.subTest(
                            schema=schema.__name__, field=name, value=identifier
                        ),
                        self.assertRaises(ValidationError),
                    ):
                        schema.model_validate(fields)

    def test_delegation_request_rejects_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            DelegationRequest.model_validate(
                {
                    "analysis_id": "sales",
                    "agent_type": "explorer",
                    "session_id": "base",
                    "message": "query data",
                    "checkpoint_ns": "attacker-controlled",
                }
            )

    def test_runtime_keeps_specialist_capabilities_scoped_to_each_role(self) -> None:
        models: dict[AgentType, BaseChatModel] = {
            kind: RecordingChatModel() for kind in AGENT_TYPES
        }
        backend = MagicMock()
        sandbox = MagicMock(get_backend=AsyncMock(return_value=backend))
        checkpointer = MagicMock()
        factory = asyncio.run(_create_runtime(models, sandbox, checkpointer))
        self.addCleanup(lambda: asyncio.run(factory.close()))
        for kind in AGENT_TYPES:
            with (
                self.subTest(agent_type=kind),
                patch("app.assistant.agents.runtime.create_agent") as create,
            ):
                run = asyncio.run(
                    factory.create_specialist(
                        AgentSessionKey(12, _CONVERSATION_ID, "sales", kind, "base")
                    )
                )
                args = create.call_args.kwargs
                self.assertIs(run.agent, create.return_value)
                self.assertIs(args["model"], models[kind])
                self.assertIs(args["backend"], backend)
                self.assertIs(args["checkpointer"], checkpointer)
                self.assertIs(args["shell_jobs"], run.shell_jobs)
                self.assertEqual(args["name"], kind)
                self.assertEqual(
                    {tool.name for tool in args["tools"]},
                    {
                        "recall_context",
                        "list_recalls",
                        "get_recall",
                        "merge_recalls",
                        "delete_recalls",
                        "execute_sql",
                        "mcp_web_search",
                    }
                    if kind == "explorer"
                    else set(),
                )
                mount = args["skill_mount"]
                if kind == "analyst":
                    self.assertEqual(f"{mount.target}/", agent_skills_mount_path(kind))
                    self.assertTrue(mount.source.is_dir())
                else:
                    self.assertIsNone(mount)

    def test_specialist_agents_expose_shell_and_file_tools(self) -> None:
        from deepagents.backends import LocalShellBackend
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import InMemorySaver

        required_tools = {
            "read_file",
            "write_file",
            "edit_file",
            "shell",
            "list_shell_jobs",
            "get_shell_job",
            "cancel_shell_job",
            "view_image",
        }

        with tempfile.TemporaryDirectory() as workspace:
            for agent_type in AGENT_TYPES:
                with self.subTest(agent_type=agent_type):
                    model = RecordingChatModel(
                        profile={
                            "image_inputs": True,
                            "image_tool_message": True,
                            "structured_output": True,
                        },
                    )
                    shell_backend = LocalShellBackend(root_dir=workspace)
                    cast(Any, shell_backend).workspace_dir = workspace
                    cast(Any, shell_backend).conversation_dir = workspace
                    cast(Any, shell_backend).shell_jobs = shell_backend
                    sandbox = MagicMock()
                    sandbox.get_backend = AsyncMock(return_value=shell_backend)
                    factory = asyncio.run(
                        _create_runtime(
                            {kind: model for kind in AGENT_TYPES},
                            sandbox,
                            InMemorySaver(),
                        )
                    )
                    self.addCleanup(
                        lambda factory=factory: asyncio.run(factory.close())
                    )
                    run = asyncio.run(
                        factory.create_specialist(
                            AgentSessionKey(
                                user_id=12,
                                conversation_id=_CONVERSATION_ID,
                                analysis_id="test",
                                agent_type=agent_type,
                                session_id="tools",
                            )
                        )
                    )
                    graph = run.agent

                    state = graph.invoke(
                        {"messages": [HumanMessage(content="inspect tools")]},
                        {"configurable": {"thread_id": agent_type}},
                    )

                    self.assertTrue(required_tools.issubset(model.seen_tools))
                    self.assertNotIn("task", model.seen_tools)
                    self.assertNotIn("SpecialistResult", model.seen_tools)
                    self.assertNotIn("response_format", model.seen_bind_kwargs)
                    self.assertEqual(state["messages"][-1].content, "done")
                    model.response_content = "second answer"
                    second = graph.invoke(
                        {"messages": [HumanMessage(content="continue")]},
                        {"configurable": {"thread_id": agent_type}},
                    )
                    self.assertEqual(second["messages"][-1].content, "second answer")

    def test_planner_exposes_direct_delegation_without_interpreter(self) -> None:
        from deepagents.backends import LocalShellBackend
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import InMemorySaver

        from app.assistant.agents.agent import create_agent
        from app.assistant.resource_loader import load_prompt

        class DirectDelegationModel(RecordingChatModel):
            def _generate(
                self,
                messages: list[BaseMessage],
                stop: list[str] | None = None,
                run_manager: Any = None,
                **kwargs: Any,
            ) -> ChatResult:
                if any(isinstance(message, ToolMessage) for message in messages):
                    return super()._generate(messages, stop, run_manager, **kwargs)
                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "id": f"delegate-{name}",
                                        "name": "delegation",
                                        "args": {"message": name},
                                    }
                                    for name in ("region", "product")
                                ],
                            )
                        )
                    ]
                )

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

        model = DirectDelegationModel(
            profile={
                "image_inputs": True,
                "image_tool_message": True,
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            backend = LocalShellBackend(root_dir=workspace)
            cast(Any, backend).workspace_dir = workspace
            cast(Any, backend).conversation_dir = workspace
            planner_shell_jobs = MagicMock(spec=ShellJobRuntime)
            planner_shell_jobs.list.return_value = []
            graph = create_agent(
                name="planner",
                system_prompt=load_prompt("agents/planner"),
                filesystem_tools=["read_file"],
                model=model,
                tools=[delegation, list_sessions, delete_session],
                backend=cast(Any, backend),
                checkpointer=InMemorySaver(),
                shell_jobs=planner_shell_jobs,
            )

            state = graph.invoke(
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
                "list_shell_jobs",
                "get_shell_job",
                "cancel_shell_job",
            }.issubset(model.seen_tools)
        )
        self.assertIn("delegation", model.seen_tools)
        self.assertIn("list_sessions", model.seen_tools)
        self.assertIn("delete_session", model.seen_tools)
        self.assertIn("view_image", model.seen_tools)
        self.assertNotIn("eval", model.seen_tools)
        self.assertEqual(
            {
                message.tool_call_id: message.content
                for message in state["messages"]
                if isinstance(message, ToolMessage)
            },
            {"delegate-region": "region", "delegate-product": "product"},
        )


class AgentSessionServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证 Session 隔离、并发、委派续接与结果持久化。"""

    async def test_runtime_builds_a_fresh_specialist_per_call(self) -> None:
        built_agents: list[CompiledStateGraph] = []

        def build_agent(**kwargs: object) -> CompiledStateGraph:
            del kwargs
            agent = cast(CompiledStateGraph, _FakeAgent())
            built_agents.append(agent)
            return agent

        builder_patch = patch(
            "app.assistant.agents.runtime.create_agent",
            side_effect=build_agent,
        )
        builder_patch.start()
        self.addCleanup(builder_patch.stop)
        model = RecordingChatModel()
        models: dict[AgentType, BaseChatModel] = {
            agent_type: model for agent_type in AGENT_TYPES
        }
        sandbox = MagicMock()

        async def get_backend(
            user_id: int,
            conversation_id: UUID,
            *,
            scope: SandboxSessionScope,
        ) -> MagicMock:
            del user_id
            backend = MagicMock()
            backend.workspace_dir = scope.workspace_path(conversation_id)
            backend.conversation_dir = f"/data/{conversation_id}"
            return backend

        sandbox.get_backend = AsyncMock(side_effect=get_backend)
        factory = await _create_runtime(models, sandbox, MagicMock())
        self.addAsyncCleanup(factory.close)
        region = AgentSessionKey(
            user_id=12,
            conversation_id=_CONVERSATION_ID,
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )
        product = AgentSessionKey(
            user_id=12,
            conversation_id=_CONVERSATION_ID,
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="product",
        )

        first_region, second_region, product_agent = await asyncio.gather(
            factory.create_specialist(region),
            factory.create_specialist(region),
            factory.create_specialist(product),
        )

        self.assertIsNot(first_region, second_region)
        self.assertIsNot(first_region, product_agent)
        self.assertEqual(len(built_agents), 3)
        self.assertEqual(sandbox.get_backend.await_count, 3)

    async def test_list_sessions_reads_persisted_states_and_analysis_filter(
        self,
    ) -> None:
        fake = _FakeAgent()
        completed_ns = "subagents/sales-decline/analyst/region"
        interrupted_ns = "subagents/inventory/explorer/base"
        fake.persisted_sessions.update({completed_ns, interrupted_ns})
        invalid_namespaces = {
            "subagents/INVALID/analyst/region",
            "subagents/sales/unknown/region",
            "subagents/sales/analyst/..",
            "subagents/sales/analyst/region/nested",
        }
        fake.persisted_sessions.update(invalid_namespaces)
        for namespace in invalid_namespaces:
            fake.checkpoints[namespace] = {
                "ts": "2026-08-29T12:00:00+00:00",
                "channel_values": {},
            }
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
                        "result": {
                            "analysis_id": "sales-decline",
                            "agent_type": "analyst",
                            "session_id": "region",
                            "status": "completed",
                            "content": "region complete",
                        },
                    }
                },
            },
        }
        fake.checkpoints[interrupted_ns] = {
            "ts": "2026-08-29T12:01:00+00:00",
            "channel_values": {},
        }
        service = _service(fake)

        all_sessions = await service.sessions.list_sessions(None)
        filtered = await service.sessions.list_sessions("sales-decline")

        self.assertEqual(
            [session.status for session in all_sessions.sessions],
            ["interrupted", "completed"],
        )
        self.assertEqual(len(filtered.sessions), 1)
        self.assertEqual(filtered.sessions[0].summary, "region complete")

    async def test_list_sessions_reads_latest_delegation_record_result(self) -> None:
        fake = _FakeAgent()
        namespace = "subagents/sales-decline/analyst/region"
        context = DelegationMessageContext(delegation_id="delegation-latest")
        fake.persisted_sessions.add(namespace)
        fake.checkpoints[namespace] = {
            "ts": "2026-08-29T12:00:00+00:00",
            "channel_values": {
                "messages": [
                    HumanMessage(
                        content="latest",
                        additional_kwargs={
                            DELEGATION_CONTEXT_KEY: context.model_dump(mode="json")
                        },
                    )
                ],
                "delegation_records": {
                    "delegation-latest": {
                        "delegation_id": "delegation-latest",
                        "status": "completed",
                        "result": {
                            "analysis_id": "sales-decline",
                            "agent_type": "analyst",
                            "session_id": "region",
                            "status": "completed",
                            "content": "latest answer",
                        },
                    }
                },
            },
        }

        sessions = await _service(fake).sessions.list_sessions("sales-decline")

        self.assertEqual(len(sessions.sessions), 1)
        self.assertEqual(sessions.sessions[0].status, "completed")
        self.assertEqual(sessions.sessions[0].summary, "latest answer")

    async def test_list_sessions_reports_active_session_before_checkpoint(
        self,
    ) -> None:
        fake = _FakeAgent(delay=0.05)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        delegation = asyncio.create_task(
            service.executor.execute_delegation(_request("region"), config)
        )
        await asyncio.sleep(0.01)
        listed = await service.sessions.list_sessions("sales-decline")
        await delegation

        self.assertEqual(len(listed.sessions), 1)
        self.assertEqual(listed.sessions[0].status, "active")

    async def test_list_sessions_survives_service_recreation(self) -> None:
        fake = _FakeAgent()
        config = build_planner_config(12, _CONVERSATION_ID)
        first_service = _service(fake)

        await first_service.executor.execute_delegation(_request("region"), config)

        recreated_service = _service(fake)
        listed = await recreated_service.sessions.list_sessions("sales-decline")

        self.assertEqual(len(listed.sessions), 1)
        self.assertEqual(listed.sessions[0].session_id, "region")
        self.assertEqual(listed.sessions[0].status, "completed")

    async def test_delete_session_removes_state_and_allows_clean_same_id(
        self,
    ) -> None:
        fake = _FakeAgent()
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )

        await service.executor.execute_delegation(_request("region"), config)
        deleted = await service.sessions.delete_session(request)
        listed = await service.sessions.list_sessions("sales-decline")
        deleted_again = await service.sessions.delete_session(request)
        recreated = await service.executor.execute_delegation(
            _request("region"), config
        )

        self.assertTrue(deleted.existed)
        self.assertFalse(deleted_again.existed)
        self.assertEqual(listed.sessions, [])
        self.assertEqual(recreated.status, "completed")
        configurable = fake.configs[-1].get("configurable", {})
        self.assertEqual(configurable.get("session_id"), request.session_id)

    async def test_delete_session_retry_finishes_partial_cleanup(self) -> None:
        fake = _FakeAgent()
        store = _FakeSessionStore(fake)
        store.workspace_delete_failures = 1
        service = _service(fake, session_store=store)
        config = build_planner_config(12, _CONVERSATION_ID)
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )

        await service.executor.execute_delegation(_request("region"), config)
        with self.assertRaisesRegex(RuntimeError, "删除 Session 工作区失败") as ctx:
            await service.sessions.delete_session(request)
        retried = await service.sessions.delete_session(request)

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
            service.executor.execute_delegation(_request("region"), config)
        )
        await asyncio.sleep(0.005)
        with self.assertRaisesRegex(RuntimeError, "Session 正在执行或删除"):
            await service.sessions.delete_session(request)
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
            service.executor.execute_delegation(_request("region"), config),
            service.executor.execute_delegation(_request("region"), config),
            service.executor.execute_delegation(_request("product"), config),
        )

        self.assertEqual(
            [result.status for result in results].count("completed"),
            2,
        )
        failed = next(result for result in results if result.status == "failed")
        self.assertIn("Session 正在执行或删除", failed.content)
        region_ns = "subagents/sales-decline/analyst/region"
        self.assertEqual(fake.max_active_by_namespace[region_ns], 1)
        self.assertGreaterEqual(fake.max_active, 2)

    async def test_parallelism_limit_rejects_excess_sessions(self) -> None:
        fake = _FakeAgent(delay=0.02)
        service = _service(fake, max_parallel_sessions=1)
        config = build_planner_config(12, _CONVERSATION_ID)
        results = await asyncio.gather(
            service.executor.execute_delegation(_request("region"), config),
            service.executor.execute_delegation(_request("product"), config),
            service.executor.execute_delegation(_request("channel"), config),
        )

        self.assertEqual(fake.max_active, 1)
        self.assertEqual(
            [result.status for result in results],
            ["completed", "failed", "failed"],
        )
        self.assertTrue(
            all("并行 Session 已满" in result.content for result in results[1:])
        )

    async def test_session_limit_rejects_new_id_but_allows_existing_session(
        self,
    ) -> None:
        fake = _FakeAgent()
        service = _service(fake, max_sessions=1)
        config = build_planner_config(12, _CONVERSATION_ID)

        first = await service.executor.execute_delegation(_request("region"), config)
        resumed = await service.executor.execute_delegation(_request("region"), config)
        excess = await service.executor.execute_delegation(_request("product"), config)

        self.assertEqual(first.status, "completed")
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(excess.status, "failed")
        self.assertIn("Session 数量已达上限", excess.content)

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
            first_service.executor.execute_delegation(_request("region"), first_config),
            second_service.executor.execute_delegation(
                _request("region"), second_config
            ),
        )

        namespace = "subagents/sales-decline/analyst/region"
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
        result = await service.executor.execute_delegation(_request("region"), parent)

        self.assertEqual(result.status, "completed")
        invoked = fake.configs[0]
        self.assertEqual(invoked.get("metadata"), {"trace": "kept"})
        invoked_configurable = invoked.get("configurable", {})
        parent_configurable = parent.get("configurable", {})
        self.assertEqual(
            invoked_configurable.get("checkpoint_ns"),
            "subagents/sales-decline/analyst/region",
        )
        self.assertEqual(
            invoked_configurable.get("thread_id"),
            parent_configurable.get("thread_id"),
        )
        self.assertNotIn("checkpoint_id", invoked_configurable)
        self.assertEqual(
            invoked_configurable.get(CONFIG_KEY_TASK_ID),
            "planner-tool-task",
        )
        self.assertNotIn(CONFIG_KEY_SCRATCHPAD, invoked_configurable)

    async def test_plain_final_answer_is_kept_without_retry(self) -> None:
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
            "app.assistant.execution.delegation.uuid4",
            return_value=SimpleNamespace(hex=delegation_id),
        ):
            result = await service.executor.execute_delegation(
                _request("region"),
                build_planner_config(12, _CONVERSATION_ID),
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, current_answer)
        self.assertEqual(len(fake.configs), 1)

    async def test_unfinished_tool_call_fails_without_format_retry(self) -> None:
        delegation_id = "delegation-malformed-result"
        fake = _FakeAgent(
            output={
                "messages": [
                    HumanMessage(
                        content="执行分析",
                        additional_kwargs={
                            DELEGATION_CONTEXT_KEY: {
                                "delegation_id": delegation_id,
                            }
                        },
                    ),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "execute_sql",
                                "args": {},
                                "id": "malformed-result",
                                "type": "tool_call",
                            }
                        ],
                    ),
                ]
            }
        )
        service = _service(fake)

        with patch(
            "app.assistant.execution.delegation.uuid4",
            return_value=SimpleNamespace(hex=delegation_id),
        ):
            result = await service.executor.execute_delegation(
                _request("region"),
                build_planner_config(12, _CONVERSATION_ID),
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(fake.configs), 1)

    async def test_incomplete_answers_fail_once_and_keep_failure_record(self) -> None:
        answers = [
            [],
            [AIMessage(content="   ")],
            [
                AIMessage(
                    content="cut off", response_metadata={"finish_reason": "length"}
                )
            ],
            [
                AIMessage(content="working"),
                ToolMessage(content="data", tool_call_id="sql"),
            ],
            [
                AIMessage(
                    content="blocked",
                    response_metadata={"finish_reason": "content_filter"},
                )
            ],
        ]
        for messages in answers:
            with self.subTest(messages=messages):
                fake = _FakeAgent(output={"messages": messages})
                result = await _service(fake).executor.execute_delegation(
                    _request("region"),
                    build_planner_config(12, _CONVERSATION_ID),
                    delegation_id="incomplete",
                )
                self.assertEqual(result.status, "failed")
                self.assertEqual(len(fake.inputs), 1)
                values = cast(
                    dict[str, Any],
                    fake.checkpoints["subagents/sales-decline/analyst/region"][
                        "channel_values"
                    ],
                )
                self.assertEqual(
                    values["delegation_records"]["incomplete"]["status"], "failed"
                )

    async def test_json_shaped_text_is_not_parsed_as_result_protocol(self) -> None:
        answer = '  {"status":"example","content":"示例文本"}  '
        fake = _FakeAgent(output={"messages": [AIMessage(content=answer)]})
        result = await _service(fake).executor.execute_delegation(
            _request("region"), build_planner_config(12, _CONVERSATION_ID)
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, answer)
        self.assertEqual(len(fake.inputs), 1)

    async def test_file_directive_is_returned_as_original_text(self) -> None:
        fake = _FakeAgent()
        result = await _service(fake).executor.execute_delegation(
            _request("region"), build_planner_config(12, _CONVERSATION_ID)
        )
        self.assertEqual(result.status, "completed")
        self.assertIn("[[DATAAGENT_ARTIFACT:", result.content)
        self.assertEqual(len(fake.inputs), 1)

    async def test_repair_request_is_plain_text_after_service_restart(self) -> None:
        fake = _FakeAgent()
        first_service = _service(fake)
        first_config = build_planner_config(12, _CONVERSATION_ID)
        created = await first_service.executor.execute_delegation(
            _request("base", agent_type="explorer"),
            first_config,
        )
        self.assertEqual(created.status, "completed")

        fake.output = {
            "messages": [
                AIMessage(
                    content="输入缺少维度，请 Planner 续接 explorer/base，补充区域字段。"
                )
            ]
        }
        restarted_service = _service(fake)
        restarted_config = build_planner_config(12, _CONVERSATION_ID)
        result = await restarted_service.executor.execute_delegation(
            _request("region"),
            restarted_config,
        )

        self.assertEqual(result.status, "completed")
        self.assertIn("explorer/base", result.content)

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

        result = await service.executor.execute_delegation(
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

        await service.executor.execute_delegation(
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

    async def test_history_reads_activity_without_a_runtime_cache(
        self,
    ) -> None:
        fake = _FakeAgent()
        namespace = "subagents/sales-decline/analyst/region"
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
        key = AgentSessionKey(
            12, _CONVERSATION_ID, "sales-decline", "analyst", "region"
        )
        with manager._activity.track(key):
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
        interrupted = await manager.read_delegation_activity(
            12, _CONVERSATION_ID, "sales-decline", "analyst", "region", "running-work"
        )
        assert interrupted is not None
        self.assertEqual(interrupted.status, "cancelled")

    async def test_read_delegation_activity_segments_checkpoint_history(self) -> None:
        fake = _FakeAgent()
        namespace = "subagents/sales-decline/analyst/region"
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
                    "result": {
                        "analysis_id": "sales-decline",
                        "agent_type": "analyst",
                        "session_id": "region",
                        "status": "completed",
                        "content": "第一轮完成",
                    },
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
                    id="provider-final-response",
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
        self.assertEqual(activity.messages[:2], [first_ai, first_tool])
        self.assertEqual(activity.messages[-1].content, "第一轮完成")
        self.assertEqual(activity.status, "completed")
        self.assertIsNone(missing)
        self.assertEqual(fake.state_configs, [])

    async def test_read_delegation_activity_keeps_unfinished_older_run_cancelled(
        self,
    ) -> None:
        fake = _FakeAgent()
        namespace = "subagents/sales-decline/analyst/region"
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
                AIMessage(
                    id="pending-tool-call",
                    content="",
                    tool_calls=[
                        {
                            "id": "pending-query-call",
                            "name": "execute_sql",
                            "args": {"status": "completed"},
                        }
                    ],
                ),
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
            activity.messages[:1], [AIMessage(id="first-ai", content="尚未完成")]
        )
        self.assertEqual(activity.status, "cancelled")

    async def test_read_delegation_activity_keeps_final_text_and_reasoning(
        self,
    ) -> None:
        fake = _FakeAgent()
        namespace = "subagents/sales-decline/analyst/region"
        fake.state_values[namespace] = {
            "delegation_records": {
                "delegation-first": {
                    "delegation_id": "delegation-first",
                    "status": "completed",
                    "result": {
                        "analysis_id": "sales-decline",
                        "agent_type": "analyst",
                        "session_id": "region",
                        "status": "completed",
                        "content": "完成",
                    },
                }
            },
            "messages": [
                HumanMessage(
                    content="review",
                    additional_kwargs={
                        DELEGATION_CONTEXT_KEY: DelegationMessageContext(
                            delegation_id="delegation-first"
                        ).model_dump(mode="json")
                    },
                ),
                AIMessage(
                    id="final-response",
                    content=[
                        {
                            "type": "reasoning",
                            "reasoning": "检查完成，准备返回结果。",
                        },
                        {"type": "text", "text": "完成"},
                    ],
                ),
            ],
        }

        activity = await _history_manager(fake).read_delegation_activity(
            12,
            _CONVERSATION_ID,
            "sales-decline",
            "analyst",
            "region",
            "delegation-first",
        )

        assert activity is not None
        self.assertEqual(activity.status, "completed")
        self.assertEqual(len(activity.messages), 1)
        reasoning_message = cast(AIMessage, activity.messages[0])
        self.assertEqual(reasoning_message.id, "final-response")
        self.assertEqual(reasoning_message.tool_calls, [])
        self.assertEqual(
            reasoning_message.content,
            [
                {"type": "reasoning", "reasoning": "检查完成，准备返回结果。"},
                {"type": "text", "text": "完成"},
            ],
        )

    async def test_replayed_delegation_reuses_saved_text_without_model_call(
        self,
    ) -> None:
        fake = _FakeAgent()
        namespace = "subagents/sales-decline/analyst/region"
        context = DelegationMessageContext(delegation_id="delegation-replay")
        fake.state_values[namespace] = {
            "delegation_records": {
                "delegation-replay": {
                    "delegation_id": "delegation-replay",
                    "status": "completed",
                    "result": {
                        "analysis_id": "sales-decline",
                        "agent_type": "analyst",
                        "session_id": "region",
                        "status": "completed",
                        "content": "已完成的分析结果",
                    },
                }
            },
            "messages": [
                HumanMessage(
                    content="analyze",
                    additional_kwargs={
                        DELEGATION_CONTEXT_KEY: context.model_dump(mode="json")
                    },
                )
            ],
        }
        service = _service(fake)

        result = await service.executor.execute_delegation(
            _request("region"),
            build_planner_config(12, _CONVERSATION_ID),
            delegation_id="delegation-replay",
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, "已完成的分析结果")
        self.assertEqual(fake.inputs, [])
        self.assertTrue(
            all(
                config.get("configurable", {}).get(CONFIG_KEY_CHECKPOINTER)
                is fake.checkpointer
                for config in fake.state_configs
            )
        )

    async def test_delegation_conflict_emits_failed_status(self) -> None:
        fake = _FakeAgent(delay=0.05)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        activities: list[SubagentActivity] = []

        active = asyncio.create_task(
            service.executor.execute_delegation(_request("region"), config)
        )
        await asyncio.sleep(0.005)
        result = await service.executor.execute_delegation(
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

    async def test_cancelled_delegation_cleans_resources_and_releases_session(
        self,
    ) -> None:
        fake = _FakeAgent()
        service = _service(fake, max_parallel_sessions=1)
        config = build_planner_config(12, _CONVERSATION_ID)
        started = asyncio.Event()
        blocked = asyncio.Event()

        async def wait_for_cancel(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await blocked.wait()

        cleanup = DelegationExecutor._cleanup_agent_run
        with (
            patch.object(fake, "ainvoke", side_effect=wait_for_cancel),
            patch.object(
                DelegationExecutor, "_cleanup_agent_run", wraps=cleanup
            ) as cleanup_run,
        ):
            async with asyncio.timeout(3):
                task = asyncio.create_task(
                    service.executor.execute_delegation(
                        _request("region"), config, delegation_id="cancel-cleanup"
                    )
                )
                try:
                    await started.wait()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

            cleanup_run.assert_awaited_once()
            agent_run = cleanup_run.call_args.args[0]
            agent_run.shell_jobs.cleanup.assert_awaited_once()

        async with asyncio.timeout(3):
            result = await service.executor.execute_delegation(
                _request("region"), config, delegation_id="after-cancel"
            )
        self.assertEqual(result.status, "completed")

    async def test_delegation_cancellation_emits_cancelled_status(self) -> None:
        fake = _FakeAgent(delay=0.2)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        activities: list[SubagentActivity] = []

        task = asyncio.create_task(
            service.executor.execute_delegation(
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
        channels = fake.checkpoints["subagents/sales-decline/analyst/region"][
            "channel_values"
        ]
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
        tombstones.delete_by_user = AsyncMock()
        persistence = MagicMock()
        persistence.checkpointer.adelete_thread = AsyncMock()
        locks = MagicMock()
        locks.advisory_lock = lambda *args, **kwargs: distributed_locks.acquire(
            "conversation"
        )
        deleting_worker = AgentManager(persistence, tombstones, locks, MagicMock())
        serving_worker = AgentManager(MagicMock(), tombstones, locks, MagicMock())

        await deleting_worker.delete_agent(12, _CONVERSATION_ID)

        with self.assertRaisesRegex(RuntimeError, "已被删除"):
            async with serving_worker.use_runtime(12, _CONVERSATION_ID):
                self.fail("deleted conversation entered execution")
        persistence.checkpointer.adelete_thread.assert_awaited_once()
