"""Dynamic Subagents 协议和 Session 编排单元测试。"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection
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
from langgraph.constants import CONFIG_KEY_CHECKPOINTER
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field, ValidationError

from app.assistant.agents.checkpoint_reader import CheckpointState
from app.assistant.agents.contracts import (
    DELEGATION_CONTEXT_KEY,
    EVAL_DELEGATIONS_KEY,
    ArtifactReference,
    ConversationAgentRuntime,
    DelegationMessageContext,
    DelegationRequest,
    DelegationResult,
    DeleteSessionRequest,
    EvalDelegationRecord,
    RepairRequest,
    SpecialistResult,
    SubagentActivity,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
    build_planner_config,
)
from app.assistant.agents.filesystem import agent_skills_mount_path
from app.assistant.agents.manager import AgentManager
from app.assistant.agents.middleware.eval_delegations import EvalDelegationMiddleware
from app.assistant.agents.session_service import AgentSessionService
from app.assistant.agents.session_store import AgentSessionStore
from app.assistant.agents.shell_jobs import ShellJobRuntime
from app.assistant.agents.specialist_agent import _specialist_response_format
from app.assistant.agents.specialists import (
    SpecialistAgentFactory,
    SpecialistAgentRun,
    SpecialistDefinition,
    build_specialist_definitions,
)
from app.shared.contracts.analysis import AGENT_TYPES, AgentSessionKey, AgentType

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
_CONVERSATION_ROOT = f"/data/{_CONVERSATION_ID}"


class RecordingChatModel(BaseChatModel):
    """记录模型请求实际可见的 Tool。"""

    seen_tools: list[str] = Field(default_factory=list)
    seen_tool_choice: str | None = None
    seen_bind_kwargs: dict[str, Any] = Field(default_factory=dict)
    response_content: str = "done"

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
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=self.response_content))
            ]
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
                    "structured_response": SpecialistResult(
                        status="completed",
                        content="analysis complete",
                        artifacts=[ArtifactReference(path=artifact_path)],
                    )
                }
            structured_response = (
                output.get("structured_response")
                if isinstance(output, dict)
                else output
            )
            existing = self.checkpoints.get(namespace, {}).get("channel_values")
            channel_values = dict(existing) if isinstance(existing, dict) else {}
            channel_values.update(
                {
                    "structured_response": structured_response,
                    "messages": [
                        *(
                            input.get("messages", [])
                            if isinstance(input.get("messages"), list)
                            else []
                        ),
                        *self.stream_messages,
                    ],
                }
            )
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
            return output
        finally:
            self.active_by_namespace[namespace] -= 1
            self.active -= 1

    async def astream(
        self,
        input: dict[str, Any],
        config: RunnableConfig,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
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
        values = output if isinstance(output, dict) else {"structured_response": output}
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


class _DistributedLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[None, None]:
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
        return self.acquire(session_key.checkpoint_ns)


async def _conversation_not_deleted() -> bool:
    return False


class _FakeSessionStore:
    def __init__(
        self,
        fake: _FakeAgent,
        *,
        artifact_verifier: Callable[[Collection[str]], Awaitable[set[str]]]
        | None = None,
        lock_factory: Callable[
            [AgentSessionKey],
            AbstractAsyncContextManager[None],
        ]
        | None = None,
    ) -> None:
        self._fake = fake
        self._artifact_verifier = artifact_verifier
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
        namespace = session_key.checkpoint_ns
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
        namespace = session_key.checkpoint_ns
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
        namespace = session_key.checkpoint_ns
        existed = namespace in self._fake.workspace_sessions
        self._fake.workspace_sessions.discard(namespace)
        return existed

    async def find_missing_files(self, paths: Collection[str]) -> set[str]:
        if self._artifact_verifier is None:
            return set()
        return await self._artifact_verifier(paths)

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
    ) -> AsyncGenerator[None, None]:
        lock = self._session_locks.setdefault(session_key.checkpoint_ns, asyncio.Lock())
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
    ) -> AsyncGenerator[None, None]:
        namespace = session_key.checkpoint_ns
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
    artifacts_exist: bool = True,
    artifact_verifier: Callable[[Collection[str]], Awaitable[set[str]]] | None = None,
    session_store: AgentSessionStore | None = None,
    session_lock_factory: Callable[
        [AgentSessionKey],
        AbstractAsyncContextManager[None],
    ]
    | None = None,
) -> AgentSessionService:
    async def find_missing_files(paths: Collection[str]) -> set[str]:
        return set() if artifacts_exist else set(paths)

    graph = cast(CompiledStateGraph, fake)

    async def build_agent(session_key: AgentSessionKey) -> SpecialistAgentRun:
        del session_key
        shell_jobs = MagicMock()
        shell_jobs.cleanup = AsyncMock()
        return SpecialistAgentRun(
            agent=graph,
            shell_jobs=cast(Any, shell_jobs),
        )

    return AgentSessionService(
        build_agent=build_agent,
        session_store=session_store
        or _FakeSessionStore(
            fake,
            artifact_verifier=artifact_verifier or find_missing_files,
            lock_factory=session_lock_factory,
        ),
        user_id=12,
        conversation_id=_CONVERSATION_ID,
        max_parallel_sessions=max_parallel_sessions,
        max_sessions=max_sessions,
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
            key.checkpoint_ns,
            "subagents/sales-decline_2026/analyst/product-category",
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

    def test_repair_request_rejects_extra_fields(self) -> None:
        repair = RepairRequest.model_validate(
            {
                "target_agent_type": "explorer",
                "target_session_id": "base",
                "reason": "region field is missing",
                "expected_result": "add region_name",
            }
        )
        result = SpecialistResult(
            status="needs_repair",
            content="region field is missing",
            repair_requests=[repair],
        )
        self.assertEqual(result.repair_requests, [repair])

        with self.assertRaises(ValidationError):
            RepairRequest.model_validate(
                {
                    "target_agent_type": "explorer",
                    "target_session_id": "base",
                    "reason": "region field is missing",
                    "evidence": [],
                    "expected_result": "add region_name",
                }
            )

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

    def test_specialist_result_enforces_status_payload(self) -> None:
        completed = SpecialistResult(
            status="completed",
            content="conclusion only",
        )
        self.assertEqual(completed.artifacts, [])
        self.assertEqual(completed.warnings, [])
        with self.assertRaises(ValidationError):
            SpecialistResult(
                status="needs_repair",
                content="missing input",
                repair_requests=[],
            )

    def test_specialist_profile_selects_native_structured_output(self) -> None:
        from langchain.agents import create_agent

        expected = SpecialistResult(
            status="completed",
            content="analysis complete",
        )
        model = RecordingChatModel(
            profile={"structured_output": True},
            response_content=expected.model_dump_json(),
        )
        agent = create_agent(
            model=model,
            tools=[execute_sql],
            response_format=_specialist_response_format(model),
        )

        state = agent.invoke({"messages": [HumanMessage(content="analyze")]})

        self.assertEqual(state["structured_response"], expected)
        self.assertNotIn("SpecialistResult", model.seen_tools)
        self.assertIsNone(model.seen_tool_choice)
        self.assertEqual(
            model.seen_bind_kwargs["response_format"]["type"],
            "json_schema",
        )
        self.assertEqual(
            model.seen_bind_kwargs["response_format"]["json_schema"]["name"],
            "SpecialistResult",
        )
        self.assertTrue(
            model.seen_bind_kwargs["response_format"]["json_schema"]["strict"]
        )

    def test_specialist_profile_without_native_selects_tool_output(self) -> None:
        from langchain.agents.structured_output import ToolStrategy

        model = RecordingChatModel(profile={"structured_output": False})

        response_format = _specialist_response_format(model)

        self.assertIsInstance(response_format, ToolStrategy)
        self.assertEqual(response_format.schema, SpecialistResult)
        self.assertTrue(response_format.handle_errors)

    def test_specialist_definitions_assign_data_tools_only_to_explorer(self) -> None:
        definitions = build_specialist_definitions(
            [
                recall_context,
                execute_sql,
            ],
            [mcp_web_search],
        )

        self.assertEqual(
            definitions["explorer"].tool_names,
            {
                "recall_context",
                "execute_sql",
                "mcp_web_search",
            },
        )
        self.assertEqual(
            definitions["analyst"].tool_names,
            set(),
        )
        self.assertEqual(
            definitions["reviewer"].tool_names,
            set(),
        )

    def test_specialist_definitions_assign_analysis_skill_only_to_analyst(
        self,
    ) -> None:
        definitions = build_specialist_definitions(
            [
                recall_context,
                execute_sql,
            ],
            [],
        )

        self.assertEqual(
            definitions["analyst"].skills,
            (agent_skills_mount_path("analyst"),),
        )
        self.assertEqual(definitions["explorer"].skills, ())
        self.assertEqual(definitions["reviewer"].skills, ())

    def test_specialist_definitions_require_explorer_data_tools(self) -> None:
        with self.assertRaisesRegex(ValueError, "Explorer 缺少必需工具"):
            build_specialist_definitions([recall_context], [])

    def test_specialist_definitions_reject_reserved_mcp_tool_names(self) -> None:
        @tool("shell")
        def conflicting_mcp_tool(command: str) -> str:
            """模拟与 Shell 工具冲突的 MCP 工具。"""
            return command

        with self.assertRaisesRegex(ValueError, "冲突"):
            build_specialist_definitions(
                [
                    recall_context,
                    execute_sql,
                ],
                [conflicting_mcp_tool],
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

        from app.assistant.agents.analyst.agent import create_analyst_agent
        from app.assistant.agents.explorer.agent import create_explorer_agent
        from app.assistant.agents.reviewer.agent import create_reviewer_agent

        register_harness_profile(
            "recordingchatmodel",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
            ),
        )
        builders = {
            "explorer": create_explorer_agent,
            "analyst": create_analyst_agent,
            "reviewer": create_reviewer_agent,
        }
        definitions = build_specialist_definitions(
            [recall_context, execute_sql],
            [],
        )
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
            for agent_type, builder in builders.items():
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
                    graph = builder(
                        model=model,
                        tools=[],
                        backend=cast(Any, shell_backend),
                        checkpointer=InMemorySaver(),
                        shell_jobs=ShellJobRuntime(cast(Any, shell_backend)),
                        skills=definitions[cast(AgentType, agent_type)].skills,
                    )

                    graph.invoke(
                        {"messages": [HumanMessage(content="inspect tools")]},
                        {"configurable": {"thread_id": agent_type}},
                    )

                    self.assertTrue(required_tools.issubset(model.seen_tools))
                    self.assertNotIn("task", model.seen_tools)

    def test_planner_exposes_tools_with_only_delegation_in_ptc(self) -> None:
        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            register_harness_profile,
        )
        from deepagents.backends import LocalShellBackend
        from langchain.agents.middleware.types import AgentMiddleware
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

        @tool
        def eval(code: str) -> str:
            """模拟 Planner 解释器工具。"""
            return code

        interpreter_kwargs: dict[str, object] = {}

        class InterpreterStub(AgentMiddleware):
            """只用于验证 Planner 工具暴露边界。"""

            def __init__(self, **kwargs: object) -> None:
                interpreter_kwargs.update(kwargs)
                self.tools = [eval]

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
            cast(Any, backend).conversation_dir = workspace
            planner_shell_jobs = MagicMock(spec=ShellJobRuntime)
            planner_shell_jobs.list.return_value = []
            with patch(
                "app.assistant.agents.planner.agent.CodeInterpreterMiddleware",
                InterpreterStub,
            ):
                graph = create_planner_agent(
                    model=model,
                    tools=[delegation, list_sessions, delete_session],
                    backend=cast(Any, backend),
                    checkpointer=InMemorySaver(),
                    session_service=_service(_FakeAgent()),
                    shell_jobs=planner_shell_jobs,
                    interpreter_memory_limit_bytes=2 * 1024 * 1024,
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
                "list_shell_jobs",
                "get_shell_job",
                "cancel_shell_job",
            }.issubset(model.seen_tools)
        )
        self.assertIn("delegation", model.seen_tools)
        self.assertIn("list_sessions", model.seen_tools)
        self.assertIn("delete_session", model.seen_tools)
        self.assertIn("view_image", model.seen_tools)
        self.assertEqual(interpreter_kwargs["ptc"], ["delegation"])


class AgentSessionServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证 Session 隔离、并发和修补限制。"""

    def test_collects_eval_delegations_until_parent_result_is_persisted(
        self,
    ) -> None:
        service = _service(_FakeAgent())
        request = DelegationRequest(
            analysis_id="sales",
            agent_type="explorer",
            session_id="source",
            message="定位销售数据",
        )
        result = service._failed_result(request, "执行失败", "RuntimeError: failed")

        service.begin_eval_delegation("eval-1", "ptc-delegation-1", request)
        service.finish_eval_delegation("eval-1", "ptc-delegation-1", result)

        records = service.take_eval_delegations("eval-1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].message, "定位销售数据")
        self.assertEqual(records[0].result, result)
        self.assertEqual(service.take_eval_delegations("eval-1"), [])

    async def test_eval_middleware_persists_collected_delegations(self) -> None:
        service = MagicMock(spec=AgentSessionService)
        service.take_eval_delegations.return_value = [
            EvalDelegationRecord(
                delegation_id="ptc-delegation-1",
                analysis_id="sales",
                agent_type="explorer",
                session_id="source",
                message="定位销售数据",
            )
        ]
        middleware = EvalDelegationMiddleware(service)
        request = cast(
            Any,
            SimpleNamespace(tool_call={"name": "eval", "id": "eval-1"}),
        )

        async def handler(_: object) -> ToolMessage:
            return ToolMessage(content="done", tool_call_id="eval-1", name="eval")

        response = await middleware.awrap_tool_call(request, cast(Any, handler))

        self.assertIsInstance(response, ToolMessage)
        assert isinstance(response, ToolMessage)
        records = response.additional_kwargs[EVAL_DELEGATIONS_KEY]
        self.assertEqual(records[0]["delegation_id"], "ptc-delegation-1")
        service.take_eval_delegations.assert_called_once_with("eval-1")

    async def test_ptc_delegation_emits_activity_linked_to_parent_eval(self) -> None:
        from langchain.tools import ToolRuntime

        from app.assistant.agents.planner.tools import create_delegation_tool

        service = MagicMock(spec=AgentSessionService)
        activities: list[SubagentActivity] = []
        result = DelegationResult(
            status="completed",
            analysis_id="sales",
            agent_type="explorer",
            session_id="source",
            content="定位完成",
        )

        async def execute(
            request: DelegationRequest,
            _: RunnableConfig,
            *,
            delegation_id: str,
            activity_writer: Callable[[SubagentActivity], None],
        ) -> DelegationResult:
            activity_writer(
                SubagentStatusActivity(
                    delegation_id=delegation_id,
                    analysis_id=request.analysis_id,
                    agent_type=request.agent_type,
                    session_id=request.session_id,
                    status="running",
                )
            )
            return result

        service.execute_delegation = AsyncMock(side_effect=execute)
        runtime = ToolRuntime(
            state={
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "eval-1",
                                "name": "eval",
                                "args": {"code": "await tools.delegation({})"},
                            }
                        ],
                    )
                ]
            },
            context=None,
            config=build_planner_config(12, _CONVERSATION_ID),
            stream_writer=activities.append,
            tool_call_id="ptc_delegation_a1b2c3d4",
            store=None,
        )
        delegation_tool = create_delegation_tool(service)

        await cast(Any, delegation_tool).coroutine(
            runtime=runtime,
            analysis_id="sales",
            agent_type="explorer",
            session_id="source",
            message="定位销售数据",
        )

        service.begin_eval_delegation.assert_called_once()
        service.finish_eval_delegation.assert_called_once_with(
            "eval-1",
            "ptc_delegation_a1b2c3d4",
            result,
        )
        self.assertEqual(activities[0].parent_tool_call_id, "eval-1")
        self.assertEqual(activities[0].instruction, "定位销售数据")

    async def test_specialist_factory_builds_a_fresh_agent_per_call(self) -> None:
        built_agents: list[CompiledStateGraph] = []

        def build_agent(**kwargs: object) -> CompiledStateGraph:
            del kwargs
            agent = cast(CompiledStateGraph, _FakeAgent())
            built_agents.append(agent)
            return agent

        definitions: dict[AgentType, SpecialistDefinition] = {
            agent_type: SpecialistDefinition(builder=build_agent)
            for agent_type in AGENT_TYPES
        }
        model = RecordingChatModel()
        models: dict[AgentType, BaseChatModel] = {
            agent_type: model for agent_type in AGENT_TYPES
        }
        sandbox = MagicMock()

        async def get_session_backend(
            user_id: int,
            conversation_id: UUID,
            analysis_id: str,
            agent_type: AgentType,
            session_id: str,
        ) -> MagicMock:
            del user_id
            backend = MagicMock()
            backend.workspace_dir = (
                f"/data/{conversation_id}/sessions/{analysis_id}/"
                f"{agent_type}/{session_id}"
            )
            backend.conversation_dir = f"/data/{conversation_id}"
            return backend

        sandbox.get_session_backend = AsyncMock(side_effect=get_session_backend)
        factory = SpecialistAgentFactory(
            definitions,
            models,
            sandbox,
            MagicMock(),
        )
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
            factory.create(region),
            factory.create(region),
            factory.create(product),
        )

        self.assertIsNot(first_region, second_region)
        self.assertIsNot(first_region, product_agent)
        self.assertEqual(len(built_agents), 3)
        self.assertEqual(sandbox.get_session_backend.await_count, 3)

    async def test_list_sessions_reads_persisted_states_and_analysis_filter(
        self,
    ) -> None:
        fake = _FakeAgent()
        completed_ns = "subagents/sales-decline/analyst/region"
        interrupted_ns = "subagents/inventory/explorer/base"
        fake.persisted_sessions.update({completed_ns, interrupted_ns})
        fake.checkpoints[completed_ns] = {
            "ts": "2026-08-29T12:00:00+00:00",
            "channel_values": {
                "structured_response": SpecialistResult(
                    status="completed",
                    content="region complete",
                    artifacts=[
                        ArtifactReference(
                            path=(
                                f"{_CONVERSATION_ROOT}/sessions/sales-decline/"
                                "analyst/region/result.json"
                            )
                        )
                    ],
                )
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
        self.assertEqual(filtered.sessions[0].artifact_count, 1)

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

    async def test_list_sessions_survives_service_recreation(self) -> None:
        fake = _FakeAgent()
        config = build_planner_config(12, _CONVERSATION_ID)
        first_service = _service(fake)

        await first_service.execute_delegation(_request("region"), config)

        recreated_service = _service(fake)
        listed = await recreated_service.list_sessions("sales-decline")

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

        await service.execute_delegation(_request("region"), config)
        deleted = await service.delete_session(request)
        listed = await service.list_sessions("sales-decline")
        deleted_again = await service.delete_session(request)
        recreated = await service.execute_delegation(_request("region"), config)

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
        self.assertIn("Session 正在执行或删除", failed.failure_reasons[0])
        region_ns = "subagents/sales-decline/analyst/region"
        self.assertEqual(fake.max_active_by_namespace[region_ns], 1)
        self.assertGreaterEqual(fake.max_active, 2)

    async def test_parallelism_limit_rejects_excess_sessions(self) -> None:
        fake = _FakeAgent(delay=0.02)
        service = _service(fake, max_parallel_sessions=1)
        config = build_planner_config(12, _CONVERSATION_ID)
        results = await asyncio.gather(
            service.execute_delegation(_request("region"), config),
            service.execute_delegation(_request("product"), config),
            service.execute_delegation(_request("channel"), config),
        )

        self.assertEqual(fake.max_active, 1)
        self.assertEqual(
            [result.status for result in results],
            ["completed", "failed", "failed"],
        )
        self.assertTrue(
            all(
                "并行 Session 已满" in result.failure_reasons[0]
                for result in results[1:]
            )
        )

    async def test_session_limit_rejects_new_id_but_allows_existing_session(
        self,
    ) -> None:
        fake = _FakeAgent()
        service = _service(fake, max_sessions=1)
        config = build_planner_config(12, _CONVERSATION_ID)

        first = await service.execute_delegation(_request("region"), config)
        resumed = await service.execute_delegation(_request("region"), config)
        excess = await service.execute_delegation(_request("product"), config)

        self.assertEqual(first.status, "completed")
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(excess.status, "failed")
        self.assertIn("Session 数量已达上限", excess.failure_reasons[0])

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
        result = await service.execute_delegation(_request("region"), parent)

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

    async def test_self_repair_is_rejected_after_structured_retry(self) -> None:
        repair = RepairRequest(
            target_agent_type="analyst",
            target_session_id="region",
            reason="retry the same calculation",
            expected_result="replace result",
        )
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="needs_repair",
                    content="self repair requested",
                    repair_requests=[repair],
                )
            }
        )
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("修补自身", result.failure_reasons[0])
        self.assertEqual(len(fake.configs), 2)

    async def test_missing_artifact_is_filtered_without_structured_retry(self) -> None:
        fake = _FakeAgent()
        service = _service(fake, artifacts_exist=False)
        config = build_planner_config(12, _CONVERSATION_ID)
        result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, "analysis complete")
        self.assertEqual(result.artifacts, [])
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("忽略不存在的产物", result.warnings[0])
        self.assertEqual(len(fake.configs), 1)

    async def test_artifact_verification_batches_all_paths(self) -> None:
        artifacts = [
            ArtifactReference(
                path=(
                    f"{_CONVERSATION_ROOT}/sessions/sales-decline/analyst/"
                    f"region/result_{index}.json"
                )
            )
            for index in range(50)
        ]
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="completed",
                    content="analysis complete",
                    artifacts=artifacts,
                )
            }
        )
        verified_batches: list[set[str]] = []

        async def verify(paths: Collection[str]) -> set[str]:
            verified_batches.append(set(paths))
            return set()

        service = _service(fake, artifact_verifier=verify)
        config = build_planner_config(12, _CONVERSATION_ID)
        result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "completed")
        self.assertEqual(verified_batches, [{artifact.path for artifact in artifacts}])

    async def test_artifact_sanitization_keeps_valid_entries(self) -> None:
        valid_path = (
            f"{_CONVERSATION_ROOT}/sessions/sales-decline/analyst/region/"
            "valid.json"
        )
        missing_path = (
            f"{_CONVERSATION_ROOT}/sessions/sales-decline/analyst/region/"
            "missing.json"
        )
        out_of_scope_path = (
            f"{_CONVERSATION_ROOT}/sessions/sales-decline/analyst/other/"
            "foreign.json"
        )
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="completed",
                    content="analysis complete",
                    artifacts=[
                        ArtifactReference(path=valid_path),
                        ArtifactReference(path=missing_path),
                        ArtifactReference(path=out_of_scope_path),
                    ],
                )
            }
        )

        async def verify(paths: Collection[str]) -> set[str]:
            self.assertEqual(paths, {valid_path, missing_path})
            return {missing_path}

        service = _service(fake, artifact_verifier=verify)
        result = await service.execute_delegation(
            _request("region"),
            build_planner_config(12, _CONVERSATION_ID),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            [artifact.path for artifact in result.artifacts],
            [valid_path],
        )
        self.assertEqual(
            result.warnings,
            [
                f"忽略越界产物：{out_of_scope_path}",
                f"忽略不存在的产物：{missing_path}",
            ],
        )
        self.assertEqual(len(fake.configs), 1)

    async def test_relative_artifact_is_resolved_from_specialist_workspace(
        self,
    ) -> None:
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="completed",
                    content="analysis complete",
                    artifacts=[ArtifactReference(path="evidence/result.json")],
                )
            }
        )
        verified_batches: list[set[str]] = []

        async def verify(paths: Collection[str]) -> set[str]:
            verified_batches.append(set(paths))
            return set()

        service = _service(fake, artifact_verifier=verify)
        result = await service.execute_delegation(
            _request("region"),
            build_planner_config(12, _CONVERSATION_ID),
        )

        expected_path = (
            f"{_CONVERSATION_ROOT}/sessions/sales-decline/analyst/region/"
            "evidence/result.json"
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.artifacts[0].path, expected_path)
        self.assertEqual(verified_batches, [{expected_path}])

    async def test_completed_artifact_outside_session_is_filtered(self) -> None:
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="completed",
                    content="analysis complete",
                    artifacts=[ArtifactReference(path="/outputs/old.json")],
                )
            }
        )
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, "analysis complete")
        self.assertEqual(result.artifacts, [])
        self.assertEqual(result.warnings, ["忽略越界产物：/outputs/old.json"])
        self.assertEqual(len(fake.configs), 1)

    async def test_unknown_repair_target_is_rejected(self) -> None:
        repair = RepairRequest(
            target_agent_type="explorer",
            target_session_id="unknown",
            reason="missing dimension",
            expected_result="add dimension",
        )
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="needs_repair",
                    content="input is incomplete",
                    repair_requests=[repair],
                )
            }
        )
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("已存在的 Session", result.failure_reasons[0])
        self.assertEqual(len(fake.configs), 2)

    async def test_repair_target_survives_service_restart(self) -> None:
        fake = _FakeAgent()
        first_service = _service(fake)
        first_config = build_planner_config(12, _CONVERSATION_ID)
        created = await first_service.execute_delegation(
            _request("base", agent_type="explorer"),
            first_config,
        )
        self.assertEqual(created.status, "completed")
        first_service.clear()

        repair = RepairRequest(
            target_agent_type="explorer",
            target_session_id="base",
            reason="missing dimension",
            expected_result="add dimension",
        )
        fake.output = {
            "structured_response": SpecialistResult(
                status="needs_repair",
                content="input is incomplete",
                repair_requests=[repair],
            )
        }
        restarted_service = _service(fake)
        restarted_config = build_planner_config(12, _CONVERSATION_ID)
        result = await restarted_service.execute_delegation(
            _request("region"),
            restarted_config,
        )

        self.assertEqual(result.status, "needs_repair")

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
        structured_response = AIMessage(
            id="structured-response",
            content=SpecialistResult(
                status="completed",
                content="analysis complete",
            ).model_dump_json(),
        )
        fake = _FakeAgent(
            stream_messages=[
                tool_call,
                tool_call,
                tool_result,
                structured_response,
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
            ["specialist-tool-call", "specialist-tool-result"],
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

    async def test_get_delegation_activity_segments_checkpoint_history(self) -> None:
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
                    id="provider-structured-response",
                    content=SpecialistResult(
                        status="completed",
                        content="第一轮完成",
                    ).model_dump_json(),
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
        service = _service(fake)

        activity = await service.get_delegation_activity(
            "sales-decline",
            "analyst",
            "region",
            "delegation-first",
        )
        missing = await service.get_delegation_activity(
            "sales-decline",
            "analyst",
            "region",
            "delegation-missing",
        )

        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertEqual(activity.messages, [first_ai, first_tool])
        self.assertEqual(activity.status, "completed")
        self.assertIsNone(missing)
        self.assertEqual(fake.state_configs, [])

    async def test_get_delegation_activity_keeps_unfinished_older_run_cancelled(
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
                    id="invalid-structured-response",
                    content="",
                    tool_calls=[
                        {
                            "id": "invalid-result-call",
                            "name": "SpecialistResult",
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
        service = _service(fake)

        activity = await service.get_delegation_activity(
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

    async def test_get_delegation_activity_keeps_structured_response_reasoning(
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
                    id="structured-response",
                    content=[
                        {
                            "type": "reasoning",
                            "reasoning": "检查完成，准备返回结果。",
                        }
                    ],
                    tool_calls=[
                        {
                            "id": "structured-call",
                            "name": "SpecialistResult",
                            "args": {"status": "completed", "content": "完成"},
                        }
                    ],
                ),
            ],
        }

        activity = await _service(fake).get_delegation_activity(
            "sales-decline",
            "analyst",
            "region",
            "delegation-first",
        )

        assert activity is not None
        self.assertEqual(activity.status, "completed")
        self.assertEqual(len(activity.messages), 1)
        reasoning_message = cast(AIMessage, activity.messages[0])
        self.assertEqual(reasoning_message.id, "structured-response")
        self.assertEqual(reasoning_message.tool_calls, [])
        self.assertEqual(
            reasoning_message.content,
            [{"type": "reasoning", "reasoning": "检查完成，准备返回结果。"}],
        )

    async def test_replayed_delegation_reuses_latest_structured_result(self) -> None:
        fake = _FakeAgent()
        namespace = "subagents/sales-decline/analyst/region"
        context = DelegationMessageContext(delegation_id="delegation-replay")
        fake.state_values[namespace] = {
            "delegation_records": {
                "delegation-replay": {
                    "delegation_id": "delegation-replay",
                    "status": "completed",
                    "result": {
                        "status": "completed",
                        "content": "已完成的分析结果",
                        "warnings": ["历史警告"],
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
            "structured_response": SpecialistResult(
                status="completed",
                content="已完成的分析结果",
            ).model_dump(mode="json"),
        }
        service = _service(fake)

        result = await service.execute_delegation(
            _request("region"),
            build_planner_config(12, _CONVERSATION_ID),
            delegation_id="delegation-replay",
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, "已完成的分析结果")
        self.assertEqual(result.warnings, ["历史警告"])
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
        channels = fake.checkpoints["subagents/sales-decline/analyst/region"][
            "channel_values"
        ]
        assert isinstance(channels, dict)
        records = channels["delegation_records"]
        assert isinstance(records, dict)
        record = records["delegation-cancel"]
        assert isinstance(record, dict)
        self.assertEqual(record["status"], "cancelled")

    async def test_agent_manager_rejects_same_planner_across_workers(self) -> None:
        fake = _FakeAgent()
        first_service = _service(fake)
        second_service = _service(fake)
        graph = cast(CompiledStateGraph, fake)
        distributed_locks = _DistributedLockRegistry()
        first_runtime = ConversationAgentRuntime(
            planner=graph,
            session_service=first_service,
            shell_jobs=MagicMock(spec=ShellJobRuntime),
            planner_lock=lambda: distributed_locks.acquire("planner"),
            conversation_deleted=_conversation_not_deleted,
        )
        second_runtime = ConversationAgentRuntime(
            planner=graph,
            session_service=second_service,
            shell_jobs=MagicMock(spec=ShellJobRuntime),
            planner_lock=lambda: distributed_locks.acquire("planner"),
            conversation_deleted=_conversation_not_deleted,
        )
        first_manager = AgentManager(MagicMock(), MagicMock(), MagicMock())
        second_manager = AgentManager(MagicMock(), MagicMock(), MagicMock())
        active = 0
        max_active = 0

        async def run(
            manager: AgentManager,
            runtime: ConversationAgentRuntime,
        ) -> None:
            nonlocal active, max_active
            async with manager.execution(12, _CONVERSATION_ID, runtime=runtime):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

        results = await asyncio.gather(
            run(first_manager, first_runtime),
            run(second_manager, second_runtime),
            return_exceptions=True,
        )

        self.assertEqual(max_active, 1)
        self.assertEqual(
            sum(isinstance(result, RuntimeError) for result in results),
            1,
        )

    async def test_persisted_tombstone_blocks_other_worker_execution(self) -> None:
        fake = _FakeAgent()
        service = _service(fake)
        graph = cast(CompiledStateGraph, fake)
        distributed_locks = _DistributedLockRegistry()
        tombstone = False

        async def conversation_deleted() -> bool:
            return tombstone

        runtime = ConversationAgentRuntime(
            planner=graph,
            session_service=service,
            shell_jobs=MagicMock(spec=ShellJobRuntime),
            planner_lock=lambda: distributed_locks.acquire("conversation"),
            conversation_deleted=conversation_deleted,
        )
        tombstones = MagicMock()

        async def write_tombstone(*args: object, **kwargs: object) -> None:
            nonlocal tombstone
            del args, kwargs
            tombstone = True

        tombstones.save = AsyncMock(side_effect=write_tombstone)
        tombstones.exists = AsyncMock(side_effect=lambda *_: tombstone)
        tombstones.delete_by_user = AsyncMock()
        persistence = MagicMock()
        persistence.delete_thread = AsyncMock()
        persistence.advisory_lock = lambda *args, **kwargs: distributed_locks.acquire(
            "conversation"
        )
        deleting_worker = AgentManager(persistence, MagicMock(), tombstones)
        serving_worker = AgentManager(MagicMock(), MagicMock(), tombstones)

        await deleting_worker.delete_agent(12, _CONVERSATION_ID)

        with self.assertRaisesRegex(RuntimeError, "已被删除"):
            async with serving_worker.execution(
                12,
                _CONVERSATION_ID,
                runtime=runtime,
            ):
                self.fail("deleted conversation entered execution")
        persistence.delete_thread.assert_awaited_once()

    @unittest.skipUnless(
        os.getenv("RUN_QUICKJS_INTEGRATION") == "1",
        "requires QuickJS integration environment",
    )
    async def test_quickjs_bridge_calls_session_lifecycle_tools(self) -> None:
        from langchain.agents.middleware.types import ModelRequest
        from langchain.tools import ToolRuntime
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.messages import AIMessage
        from langchain_quickjs import CodeInterpreterMiddleware

        from app.assistant.agents.planner.tools import (
            create_delegation_tool,
            create_delete_session_tool,
            create_list_sessions_tool,
        )

        fake = _FakeAgent()
        service = _service(fake)
        delegation_tool = create_delegation_tool(service)
        list_sessions_tool = create_list_sessions_tool(service)
        delete_session_tool = create_delete_session_tool(service)

        @tool
        def forbidden_tool() -> str:
            """模拟未加入 PTC 白名单的 Agent Tool。"""
            return "must not be callable"

        middleware = CodeInterpreterMiddleware(
            mode="call",
            ptc=["delegation", "list_sessions", "delete_session"],
        )
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([AIMessage(content="ok")])),
            messages=[],
            tools=[
                delegation_tool,
                list_sessions_tool,
                delete_session_tool,
                forbidden_tool,
            ],
        )
        middleware._prepare_for_call(request)
        config = build_planner_config(12, _CONVERSATION_ID)
        runtime = ToolRuntime(
            state={"messages": []},
            context=None,
            config=config,
            stream_writer=lambda _: None,
            tool_call_id="eval-call",
            store=None,
        )
        eval_tool = middleware.tools[0]
        eval_coroutine = cast(Any, eval_tool).coroutine
        try:
            result = await eval_coroutine(
                runtime=runtime,
                code="""
const delegated = await tools.delegation({
  analysis_id: "sales-decline",
  agent_type: "analyst",
  session_id: "region",
  message: "analyze source",
});
const listed = await tools.listSessions({ analysis_id: "sales-decline" });
const deleted = await tools.deleteSession({
  analysis_id: "sales-decline",
  agent_type: "analyst",
  session_id: "region",
});
const forbiddenType = typeof tools.forbiddenTool;
({ delegated, listed, deleted, forbiddenType });
""",
            )
        finally:
            middleware._registry.close()

        self.assertIn("completed", str(result.content))
        self.assertIn("success", str(result.content))
        self.assertIn("undefined", str(result.content))
        self.assertEqual(len(fake.configs), 1)
        self.assertEqual(fake.persisted_sessions, set())
