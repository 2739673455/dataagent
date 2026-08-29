"""Dynamic Subagents 协议和 Session 编排单元测试"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field, ValidationError

from app.analytics.agents.contracts import (
    ArtifactReference,
    ConversationAgentRuntime,
    DelegationRequest,
    DeleteSessionRequest,
    RepairRequest,
    SpecialistResult,
    build_planner_config,
)
from app.analytics.agents.manager import AgentManager
from app.analytics.agents.session_service import AgentSessionService
from app.analytics.agents.session_store import AgentSessionStore
from app.analytics.agents.skills import agent_skills_mount_path
from app.analytics.agents.specialists import (
    SpecialistAgentFactory,
    SpecialistDefinition,
    build_specialist_definitions,
)
from app.shared.contracts.analysis import AGENT_TYPES, AgentSessionKey, AgentType

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


class RecordingChatModel(BaseChatModel):
    """记录模型请求实际可见的 Tool"""

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
        del tool_choice, kwargs
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
    """检索测试语义资源"""
    return query


@tool
def execute_sql(sql: str) -> str:
    """执行测试 SQL"""
    return sql


@tool
def mcp_web_search(query: str) -> str:
    """模拟 MCP 搜索工具"""
    return query


class _FakeAgent:
    def __init__(
        self,
        *,
        delay: float = 0,
        output: object | None = None,
    ) -> None:
        self.delay = delay
        self.output = output
        self.active = 0
        self.max_active = 0
        self.active_by_namespace: Counter[str] = Counter()
        self.max_active_by_namespace: Counter[str] = Counter()
        self.configs: list[RunnableConfig] = []
        self.persisted_sessions: set[str] = set()
        self.workspace_sessions: set[str] = set()
        self.checkpoints: dict[str, dict[str, object]] = {}

    async def ainvoke(
        self,
        input: dict[str, Any],
        config: RunnableConfig,
    ) -> object:
        del input
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
                artifact_path = (
                    f"/analyses/{configurable['analysis_id']}/sessions/"
                    f"{configurable['agent_type']}/{configurable['session_id']}/"
                    "result.json"
                )
                output = {
                    "structured_response": SpecialistResult(
                        status="completed",
                        summary="analysis complete",
                        findings=["verified finding"],
                        artifacts=[ArtifactReference(path=artifact_path)],
                        confidence="high",
                    )
                }
            structured_response = (
                output.get("structured_response")
                if isinstance(output, dict)
                else output
            )
            self.checkpoints[namespace] = {
                "ts": "2026-08-29T12:00:00+00:00",
                "channel_values": {"structured_response": structured_response},
            }
            return output
        finally:
            self.active_by_namespace[namespace] -= 1
            self.active -= 1


class _DistributedLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[None, None]:
        async with self._locks.setdefault(name, asyncio.Lock()):
            yield

    def session_lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]:
        return self.acquire(session_key.checkpoint_ns)


@asynccontextmanager
async def _unlocked_session(
    session_key: AgentSessionKey,
) -> AsyncGenerator[None, None]:
    del session_key
    yield


async def _conversation_not_deleted() -> bool:
    return False


class _FakeSessionStore:
    def __init__(
        self,
        fake: _FakeAgent,
        *,
        artifact_verifier: Callable[[str], Awaitable[bool]] | None = None,
        lock_factory: Callable[
            [AgentSessionKey],
            AbstractAsyncContextManager[None],
        ] = _unlocked_session,
    ) -> None:
        self._fake = fake
        self._artifact_verifier = artifact_verifier
        self._lock_factory = lock_factory
        self.workspace_delete_failures = 0

    async def list_namespaces(self, analysis_id: str | None) -> list[str]:
        prefix = f"subagents/{analysis_id}/" if analysis_id else "subagents/"
        return sorted(
            namespace
            for namespace in self._fake.persisted_sessions
            if namespace.startswith(prefix)
        )

    async def load_checkpoint(
        self,
        session_key: AgentSessionKey,
    ) -> dict[str, object] | None:
        return self._fake.checkpoints.get(session_key.checkpoint_ns)

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

    async def artifact_exists(self, path: str) -> bool:
        if self._artifact_verifier is None:
            return True
        return await self._artifact_verifier(path)

    def lock(
        self,
        session_key: AgentSessionKey,
    ) -> AbstractAsyncContextManager[None]:
        return self._lock_factory(session_key)


def _service(
    fake: _FakeAgent,
    *,
    max_parallel_sessions: int = 8,
    max_delegations_per_run: int = 20,
    max_repair_rounds: int = 3,
    max_repair_depth: int = 5,
    max_session_resumes: int = 3,
    session_lock_timeout: float = 1,
    artifacts_exist: bool = True,
    artifact_verifier: Callable[[str], Awaitable[bool]] | None = None,
    session_store: AgentSessionStore | None = None,
    session_lock_factory: Callable[
        [AgentSessionKey],
        AbstractAsyncContextManager[None],
    ] = _unlocked_session,
) -> AgentSessionService:
    async def artifact_exists(path: str) -> bool:
        del path
        return artifacts_exist

    graph = cast(CompiledStateGraph, fake)

    async def build_agent(session_key: AgentSessionKey) -> CompiledStateGraph:
        del session_key
        return graph

    return AgentSessionService(
        build_agent=build_agent,
        session_store=session_store
        or _FakeSessionStore(
            fake,
            artifact_verifier=artifact_verifier or artifact_exists,
            lock_factory=session_lock_factory,
        ),
        user_id=12,
        conversation_id=_CONVERSATION_ID,
        max_parallel_sessions=max_parallel_sessions,
        max_delegations_per_run=max_delegations_per_run,
        max_repair_rounds=max_repair_rounds,
        max_repair_depth=max_repair_depth,
        max_session_resumes=max_session_resumes,
        session_lock_timeout=session_lock_timeout,
    )


def _request(
    session_id: str,
    *,
    agent_type: AgentType = "analyst",
    repair_depth: int = 0,
) -> DelegationRequest:
    return DelegationRequest(
        analysis_id="sales-decline",
        agent_type=agent_type,
        session_id=session_id,
        message="analyze the supplied artifact",
        repair_depth=repair_depth,
    )


class DynamicSubagentContractTest(unittest.TestCase):
    """验证公开协议和专业 Agent 注册约束"""

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
        self.assertEqual(
            key.workspace_dir,
            "/analyses/sales-decline_2026/sessions/analyst/product-category",
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

    def test_repair_request_requires_evidence_and_rejects_extra_fields(self) -> None:
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
        with self.assertRaises(ValidationError):
            SpecialistResult(
                status="completed",
                summary="done",
                findings=[],
                artifacts=[],
            )
        with self.assertRaises(ValidationError):
            SpecialistResult(
                status="needs_repair",
                summary="missing input",
                repair_requests=[],
            )

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
        self.assertEqual(
            definitions["visualizer"].tool_names,
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
        self.assertEqual(definitions["visualizer"].skills, ())

    def test_specialist_definitions_require_explorer_data_tools(self) -> None:
        with self.assertRaisesRegex(ValueError, "Explorer 缺少必需工具"):
            build_specialist_definitions([recall_context], [])

    def test_specialist_definitions_reject_reserved_mcp_tool_names(self) -> None:
        @tool("execute")
        def conflicting_mcp_tool(command: str) -> str:
            """模拟与 Shell 工具冲突的 MCP 工具"""
            return command

        with self.assertRaisesRegex(ValueError, "冲突"):
            build_specialist_definitions(
                [
                    recall_context,
                    execute_sql,
                ],
                [conflicting_mcp_tool],
            )

    def test_specialist_agents_expose_native_execution_and_file_tools(self) -> None:
        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            register_harness_profile,
        )
        from deepagents.backends import LocalShellBackend
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import InMemorySaver

        from app.analytics.agents.analyst.agent import create_analyst_agent
        from app.analytics.agents.explorer.agent import create_explorer_agent
        from app.analytics.agents.reviewer.agent import create_reviewer_agent
        from app.analytics.agents.visualizer.agent import create_visualizer_agent

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
            "visualizer": create_visualizer_agent,
        }
        definitions = build_specialist_definitions(
            [recall_context, execute_sql],
            [],
        )
        required_tools = {
            "ls",
            "read_file",
            "write_file",
            "edit_file",
            "delete",
            "glob",
            "grep",
            "execute",
        }

        with tempfile.TemporaryDirectory() as workspace:
            for agent_type, builder in builders.items():
                with self.subTest(agent_type=agent_type):
                    model = RecordingChatModel()
                    graph = builder(
                        model=model,
                        tools=[],
                        backend=LocalShellBackend(root_dir=workspace),
                        checkpointer=InMemorySaver(),
                        skills=definitions[cast(AgentType, agent_type)].skills,
                    )

                    graph.invoke(
                        {"messages": [HumanMessage(content="inspect tools")]},
                        {"configurable": {"thread_id": agent_type}},
                    )

                    self.assertTrue(required_tools.issubset(model.seen_tools))
                    self.assertNotIn("task", model.seen_tools)

    def test_planner_does_not_expose_mutating_file_or_shell_tools(self) -> None:
        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            register_harness_profile,
        )
        from deepagents.backends import LocalShellBackend
        from langchain.agents.middleware.types import AgentMiddleware
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import InMemorySaver

        from app.analytics.agents.planner.agent import create_planner_agent

        @tool
        def delegation(message: str) -> str:
            """委派测试专业 Agent"""
            return message

        @tool
        def list_sessions() -> str:
            """查询测试专业 Session"""
            return "[]"

        @tool
        def delete_session(session_id: str) -> str:
            """删除测试专业 Session"""
            return session_id

        @tool
        def eval(code: str) -> str:
            """模拟 Planner 解释器工具"""
            return code

        class InterpreterStub(AgentMiddleware):
            """只用于验证 Planner 工具暴露边界"""

            def __init__(self, **kwargs: object) -> None:
                del kwargs
                self.tools = [eval]

        register_harness_profile(
            "recordingchatmodel",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
            ),
        )
        model = RecordingChatModel()
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.analytics.agents.planner.agent.CodeInterpreterMiddleware",
                InterpreterStub,
            ):
                graph = create_planner_agent(
                    model=model,
                    tools=[delegation, list_sessions, delete_session],
                    backend=LocalShellBackend(root_dir=workspace),
                    checkpointer=InMemorySaver(),
                    interpreter_mode="thread",
                    interpreter_ptc=[
                        "delegation",
                        "list_sessions",
                        "delete_session",
                    ],
                    interpreter_timeout_seconds=1,
                    interpreter_memory_limit_bytes=2 * 1024 * 1024,
                    interpreter_max_ptc_calls=4,
                    max_delegations_per_run=2,
                    max_repair_rounds=1,
                    max_repair_depth=1,
                )

            graph.invoke(
                {"messages": [HumanMessage(content="inspect tools")]},
                {"configurable": {"thread_id": "planner-tools"}},
            )

        self.assertTrue({"ls", "read_file", "glob", "grep"}.issubset(model.seen_tools))
        self.assertTrue(
            {"write_file", "edit_file", "delete", "execute"}.isdisjoint(
                model.seen_tools
            )
        )
        self.assertIn("delegation", model.seen_tools)
        self.assertIn("list_sessions", model.seen_tools)
        self.assertIn("delete_session", model.seen_tools)


class AgentSessionServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证 Session 隔离、并发和修补限制"""

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
        sandbox.get_session_backend = AsyncMock(return_value=MagicMock())
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
                    summary="region complete",
                    findings=["region finding"],
                    artifacts=[
                        ArtifactReference(
                            path=(
                                "/analyses/sales-decline/sessions/analyst/"
                                "region/result.json"
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
        config.setdefault("configurable", {})["planner_run_id"] = "active-run"

        async with service.planner_run("active-run"):
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
        config.setdefault("configurable", {})["planner_run_id"] = "persist-run"
        first_service = _service(fake)

        async with first_service.planner_run("persist-run"):
            await first_service.execute_delegation(_request("region"), config)

        recreated_service = _service(fake)
        listed = await recreated_service.list_sessions("sales-decline")

        self.assertEqual(len(listed.sessions), 1)
        self.assertEqual(listed.sessions[0].session_id, "region")
        self.assertEqual(listed.sessions[0].status, "completed")

    async def test_list_sessions_does_not_consume_delegation_budget(self) -> None:
        fake = _FakeAgent()
        service = _service(fake, max_delegations_per_run=1)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "list-budget"

        async with service.planner_run("list-budget"):
            await service.list_sessions(None)
            await service.list_sessions("sales-decline")
            result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "completed")

    async def test_delete_session_removes_state_and_allows_clean_same_id(
        self,
    ) -> None:
        fake = _FakeAgent()
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "delete-run"
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )

        async with service.planner_run("delete-run"):
            await service.execute_delegation(_request("region"), config)
            deleted = await service.delete_session(request, config)
            listed = await service.list_sessions("sales-decline")
            deleted_again = await service.delete_session(request, config)
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
        config.setdefault("configurable", {})["planner_run_id"] = "delete-retry"
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )

        async with service.planner_run("delete-retry"):
            await service.execute_delegation(_request("region"), config)
            with self.assertRaisesRegex(RuntimeError, "删除 Session 工作区失败") as ctx:
                await service.delete_session(request, config)
            retried = await service.delete_session(request, config)

        self.assertNotIn("sensitive container failure", str(ctx.exception))
        self.assertTrue(retried.existed)
        self.assertEqual(fake.persisted_sessions, set())
        self.assertEqual(fake.workspace_sessions, set())

    async def test_delete_session_does_not_refund_delegation_budget(self) -> None:
        fake = _FakeAgent()
        service = _service(fake, max_delegations_per_run=1)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "delete-budget"
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )

        async with service.planner_run("delete-budget"):
            first = await service.execute_delegation(_request("region"), config)
            await service.delete_session(request, config)
            second = await service.execute_delegation(_request("region"), config)

        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "failed")
        self.assertIn("委派次数上限", second.limitations[0])

    async def test_delete_session_does_not_refund_repair_round_budget(self) -> None:
        base_namespace = "subagents/sales-decline/explorer/base"
        repair = RepairRequest(
            target_agent_type="explorer",
            target_session_id="base",
            reason="source data must be refreshed",
            evidence=[
                ArtifactReference(path="/analyses/sales-decline/shared/evidence.json")
            ],
            expected_result="refresh source data",
        )
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="needs_repair",
                    summary="source data is stale",
                    repair_requests=[repair],
                )
            }
        )
        fake.persisted_sessions.add(base_namespace)
        fake.checkpoints[base_namespace] = {
            "ts": "2026-08-29T12:00:00+00:00",
            "channel_values": {},
        }
        service = _service(fake, max_repair_rounds=1)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "repair-budget"
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )

        async with service.planner_run("repair-budget"):
            first = await service.execute_delegation(_request("region"), config)
            await service.delete_session(request, config)
            second = await service.execute_delegation(_request("product"), config)

        self.assertEqual(first.status, "needs_repair")
        self.assertEqual(second.status, "failed")
        self.assertIn("max repair rounds", second.limitations[0])

    async def test_delete_session_waits_for_active_delegation(self) -> None:
        fake = _FakeAgent(delay=0.03)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "delete-race"
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )

        async with service.planner_run("delete-race"):
            delegation = asyncio.create_task(
                service.execute_delegation(_request("region"), config)
            )
            await asyncio.sleep(0.005)
            deletion = asyncio.create_task(service.delete_session(request, config))
            delegation_result, deletion_result = await asyncio.gather(
                delegation,
                deletion,
            )

        self.assertEqual(delegation_result.status, "completed")
        self.assertTrue(deletion_result.existed)
        self.assertNotIn(request.session_id, " ".join(fake.persisted_sessions))

    async def test_delete_session_timeout_is_stable_and_retryable(self) -> None:
        fake = _FakeAgent()
        distributed_locks = _DistributedLockRegistry()
        store = _FakeSessionStore(
            fake,
            lock_factory=distributed_locks.session_lock,
        )
        service = _service(
            fake,
            session_store=store,
            session_lock_timeout=0.01,
        )
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "delete-timeout"
        request = DeleteSessionRequest(
            analysis_id="sales-decline",
            agent_type="analyst",
            session_id="region",
        )
        session_key = AgentSessionKey(
            user_id=12,
            conversation_id=_CONVERSATION_ID,
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
        )

        async with service.planner_run("delete-timeout"):
            async with distributed_locks.session_lock(session_key):
                with self.assertRaisesRegex(
                    TimeoutError,
                    "Session 删除等待锁超时",
                ):
                    await service.delete_session(request, config)
            retried = await service.delete_session(request, config)

        self.assertFalse(retried.existed)

    async def test_same_session_serializes_while_other_sessions_run_parallel(
        self,
    ) -> None:
        fake = _FakeAgent(delay=0.03)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "parallel-run"

        async with service.planner_run("parallel-run"):
            results = await asyncio.gather(
                service.execute_delegation(_request("region"), config),
                service.execute_delegation(_request("region"), config),
                service.execute_delegation(_request("product"), config),
            )

        self.assertTrue(all(result.status == "completed" for result in results))
        region_ns = "subagents/sales-decline/analyst/region"
        self.assertEqual(fake.max_active_by_namespace[region_ns], 1)
        self.assertGreaterEqual(fake.max_active, 2)

    async def test_parallelism_semaphore_limits_different_sessions(self) -> None:
        fake = _FakeAgent(delay=0.02)
        service = _service(fake, max_parallel_sessions=1)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "limited-run"

        async with service.planner_run("limited-run"):
            await asyncio.gather(
                service.execute_delegation(_request("region"), config),
                service.execute_delegation(_request("product"), config),
                service.execute_delegation(_request("channel"), config),
            )

        self.assertEqual(fake.max_active, 1)

    async def test_same_session_serializes_across_service_instances(self) -> None:
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
        first_config.setdefault("configurable", {})["planner_run_id"] = "worker-one"
        second_config.setdefault("configurable", {})["planner_run_id"] = "worker-two"

        async with (
            first_service.planner_run("worker-one"),
            second_service.planner_run("worker-two"),
        ):
            await asyncio.gather(
                first_service.execute_delegation(_request("region"), first_config),
                second_service.execute_delegation(_request("region"), second_config),
            )

        namespace = "subagents/sales-decline/analyst/region"
        self.assertEqual(fake.max_active_by_namespace[namespace], 1)

    async def test_delegation_builds_controlled_subagent_config(self) -> None:
        fake = _FakeAgent()
        service = _service(fake)
        parent = build_planner_config(12, _CONVERSATION_ID)
        parent["metadata"] = {"trace": "kept"}
        parent_configurable = parent.setdefault("configurable", {})
        parent_configurable["checkpoint_id"] = "planner-checkpoint"
        parent_configurable["planner_run_id"] = "config-run"

        async with service.planner_run("config-run"):
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

    async def test_delegation_and_resume_limits_apply_per_planner_run(self) -> None:
        fake = _FakeAgent()
        service = _service(
            fake,
            max_delegations_per_run=2,
            max_session_resumes=1,
        )
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "budget-run"

        async with service.planner_run("budget-run"):
            first = await service.execute_delegation(_request("region"), config)
            second = await service.execute_delegation(_request("region"), config)
            third = await service.execute_delegation(_request("region"), config)

        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertEqual(third.status, "failed")
        self.assertIn("委派次数上限", third.limitations[0])

    async def test_session_timeout_returns_failed_protocol_result(self) -> None:
        fake = _FakeAgent(delay=0.05)
        service = _service(fake, session_lock_timeout=0.005)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "timeout-run"

        async with service.planner_run("timeout-run"):
            result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("超时", result.limitations[0])

    async def test_self_repair_is_rejected_after_structured_retry(self) -> None:
        repair = RepairRequest(
            target_agent_type="analyst",
            target_session_id="region",
            reason="retry the same calculation",
            evidence=[
                ArtifactReference(path="/analyses/sales-decline/shared/source.json")
            ],
            expected_result="replace result",
        )
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="needs_repair",
                    summary="self repair requested",
                    repair_requests=[repair],
                )
            }
        )
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "self-repair-run"

        async with service.planner_run("self-repair-run"):
            result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("修补自身", result.limitations[0])
        self.assertEqual(len(fake.configs), 2)

    async def test_missing_artifact_is_rejected_after_structured_retry(self) -> None:
        fake = _FakeAgent()
        service = _service(fake, artifacts_exist=False)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "artifact-run"

        async with service.planner_run("artifact-run"):
            result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("产物不存在", result.limitations[0])
        self.assertEqual(len(fake.configs), 2)

    async def test_artifact_verification_has_bounded_fan_out(self) -> None:
        artifacts = [
            ArtifactReference(
                path=(
                    "/analyses/sales-decline/sessions/analyst/region/"
                    f"result_{index}.json"
                )
            )
            for index in range(100)
        ]
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="completed",
                    summary="analysis complete",
                    findings=["verified finding"],
                    artifacts=artifacts,
                    confidence="high",
                )
            }
        )
        active = 0
        max_active = 0

        async def verify(path: str) -> bool:
            nonlocal active, max_active
            del path
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.001)
                return True
            finally:
                active -= 1

        service = _service(fake, artifact_verifier=verify)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "artifact-fan-out"

        async with service.planner_run("artifact-fan-out"):
            result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "completed")
        self.assertEqual(max_active, 8)

    async def test_completed_artifact_outside_session_is_rejected(self) -> None:
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="completed",
                    summary="analysis complete",
                    findings=["unscoped finding"],
                    artifacts=[ArtifactReference(path="/outputs/old.json")],
                    confidence="low",
                )
            }
        )
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "path-domain-run"

        async with service.planner_run("path-domain-run"):
            result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("超出当前分析范围", result.limitations[0])
        self.assertEqual(len(fake.configs), 2)

    async def test_unknown_repair_target_is_rejected(self) -> None:
        repair = RepairRequest(
            target_agent_type="explorer",
            target_session_id="unknown",
            reason="missing dimension",
            evidence=[
                ArtifactReference(path="/analyses/sales-decline/shared/source.json")
            ],
            expected_result="add dimension",
        )
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="needs_repair",
                    summary="input is incomplete",
                    repair_requests=[repair],
                )
            }
        )
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "unknown-target"

        async with service.planner_run("unknown-target"):
            result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("已存在的 Session", result.limitations[0])
        self.assertEqual(len(fake.configs), 2)

    async def test_repair_target_survives_service_restart(self) -> None:
        fake = _FakeAgent()
        first_service = _service(fake)
        first_config = build_planner_config(12, _CONVERSATION_ID)
        first_config.setdefault("configurable", {})["planner_run_id"] = "create-target"
        async with first_service.planner_run("create-target"):
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
            evidence=[
                ArtifactReference(path="/analyses/sales-decline/shared/source.json")
            ],
            expected_result="add dimension",
        )
        fake.output = {
            "structured_response": SpecialistResult(
                status="needs_repair",
                summary="input is incomplete",
                repair_requests=[repair],
            )
        }
        restarted_service = _service(fake)
        restarted_config = build_planner_config(12, _CONVERSATION_ID)
        restarted_config.setdefault("configurable", {})["planner_run_id"] = (
            "after-restart"
        )

        async with restarted_service.planner_run("after-restart"):
            result = await restarted_service.execute_delegation(
                _request("region"),
                restarted_config,
            )

        self.assertEqual(result.status, "needs_repair")

    async def test_repair_depth_requires_server_issued_value(self) -> None:
        repair = RepairRequest(
            target_agent_type="explorer",
            target_session_id="base",
            reason="missing dimension",
            evidence=[
                ArtifactReference(path="/analyses/sales-decline/shared/source.json")
            ],
            expected_result="add dimension",
        )
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="needs_repair",
                    summary="input is incomplete",
                    repair_requests=[repair],
                )
            }
        )
        base_namespace = "subagents/sales-decline/explorer/base"
        fake.persisted_sessions.add(base_namespace)
        fake.checkpoints[base_namespace] = {
            "ts": "2026-08-29T12:00:00+00:00",
            "channel_values": {},
        }
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "signed-depth"

        async with service.planner_run("signed-depth"):
            repair_result = await service.execute_delegation(_request("region"), config)
            wrong_depth = await service.execute_delegation(
                _request("base", agent_type="explorer"),
                config,
            )
            fake.output = None
            repaired = await service.execute_delegation(
                _request("base", agent_type="explorer", repair_depth=1),
                config,
            )
            reset_depth = await service.execute_delegation(
                _request("base", agent_type="explorer"),
                config,
            )
            continued_repair = await service.execute_delegation(
                _request("base", agent_type="explorer", repair_depth=1),
                config,
            )

        self.assertEqual(repair_result.status, "needs_repair")
        self.assertIn("修补深度必须为 1", wrong_depth.limitations[0])
        self.assertEqual(repaired.status, "completed")
        self.assertIn("修补深度必须为 1", reset_depth.limitations[0])
        self.assertEqual(continued_repair.status, "completed")

    async def test_repair_depth_limit_stops_new_repair_request(self) -> None:
        repair = RepairRequest(
            target_agent_type="explorer",
            target_session_id="base",
            reason="missing dimension",
            evidence=[
                ArtifactReference(path="/analyses/sales-decline/shared/source.json")
            ],
            expected_result="add dimension",
        )
        fake = _FakeAgent(
            output={
                "structured_response": SpecialistResult(
                    status="needs_repair",
                    summary="input is incomplete",
                    repair_requests=[repair],
                )
            }
        )
        base_namespace = "subagents/sales-decline/explorer/base"
        fake.persisted_sessions.add(base_namespace)
        fake.checkpoints[base_namespace] = {
            "ts": "2026-08-29T12:00:00+00:00",
            "channel_values": {},
        }
        service = _service(fake, max_repair_depth=0)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "repair-depth-run"

        async with service.planner_run("repair-depth-run"):
            result = await service.execute_delegation(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("max repair depth", result.limitations[0])

    async def test_missing_planner_run_id_is_rejected(self) -> None:
        fake = _FakeAgent()
        service = _service(fake)

        result = await service.execute_delegation(
            _request("region"),
            build_planner_config(12, _CONVERSATION_ID),
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("缺少 planner_run_id", result.limitations[0])
        self.assertEqual(fake.configs, [])

    async def test_agent_manager_serializes_same_planner_across_workers(self) -> None:
        fake = _FakeAgent()
        first_service = _service(fake)
        second_service = _service(fake)
        graph = cast(CompiledStateGraph, fake)
        distributed_locks = _DistributedLockRegistry()
        first_runtime = ConversationAgentRuntime(
            planner=graph,
            session_service=first_service,
            planner_lock=lambda: distributed_locks.acquire("planner"),
            conversation_deleted=_conversation_not_deleted,
        )
        second_runtime = ConversationAgentRuntime(
            planner=graph,
            session_service=second_service,
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

        await asyncio.gather(
            run(first_manager, first_runtime),
            run(second_manager, second_runtime),
        )

        self.assertEqual(max_active, 1)

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

        from app.analytics.agents.planner.delegation import (
            create_delegation_tool,
            create_delete_session_tool,
            create_list_sessions_tool,
        )

        fake = _FakeAgent()
        service = _service(fake, max_delegations_per_run=1)
        delegation_tool = create_delegation_tool(service)
        list_sessions_tool = create_list_sessions_tool(service)
        delete_session_tool = create_delete_session_tool(service)

        @tool
        def forbidden_tool() -> str:
            """模拟未加入 PTC 白名单的 Agent Tool"""
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
        config.setdefault("configurable", {})["planner_run_id"] = "bridge-run"
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
            async with service.planner_run("bridge-run"):
                result = await eval_coroutine(
                    runtime=runtime,
                    code="""
const delegated = await tools.delegation({
  analysis_id: "sales-decline",
  agent_type: "analyst",
  session_id: "region",
  message: "analyze source",
  repair_depth: 0,
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
