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
    DelegateAgentRequest,
    RepairRequest,
    SpecialistResult,
    build_planner_config,
)
from app.analytics.agents.manager import AgentManager, ConversationAgentRuntime
from app.analytics.agents.registry import AgentRegistry, build_agent_definitions
from app.analytics.agents.session_service import AgentSessionService
from app.shared.contracts.analysis import AgentSessionKey, AgentType

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
def search_semantic_resources(query: str) -> str:
    """检索测试语义资源"""
    return query


@tool
def execute_sql(sql: str) -> str:
    """执行测试 SQL"""
    return sql


@tool
def search_query_experiences(query: str) -> str:
    """检索测试查询经验"""
    return query


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
            if self.output is not None:
                return self.output
            configurable = config.get("configurable", {})
            artifact_path = (
                f"/analyses/{configurable['analysis_id']}/sessions/"
                f"{configurable['agent_type']}/{configurable['session_id']}/result.json"
            )
            return {
                "structured_response": SpecialistResult(
                    status="completed",
                    summary="analysis complete",
                    findings=["verified finding"],
                    artifacts=[ArtifactReference(path=artifact_path)],
                    confidence="high",
                )
            }
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


def _registry(fake: _FakeAgent) -> AgentRegistry:
    definitions = build_agent_definitions(
        [
            search_semantic_resources,
            search_query_experiences,
            execute_sql,
        ],
        [],
    )
    graph = cast(CompiledStateGraph, fake)

    async def build_agent(session_key: AgentSessionKey) -> CompiledStateGraph:
        del session_key
        return graph

    return AgentRegistry(definitions, build_agent)


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
    session_lock_factory: Callable[
        [AgentSessionKey],
        AbstractAsyncContextManager[None],
    ] = _unlocked_session,
    result_observer: (
        Callable[[AgentSessionKey, SpecialistResult], Awaitable[None]] | None
    ) = None,
) -> AgentSessionService:
    async def artifact_exists(path: str) -> bool:
        del path
        return artifacts_exist

    async def session_exists(session_key: AgentSessionKey) -> bool:
        return session_key.checkpoint_ns in fake.persisted_sessions

    return AgentSessionService(
        registry=_registry(fake),
        user_id=12,
        conversation_id=_CONVERSATION_ID,
        max_parallel_sessions=max_parallel_sessions,
        max_delegations_per_run=max_delegations_per_run,
        max_repair_rounds=max_repair_rounds,
        max_repair_depth=max_repair_depth,
        max_session_resumes=max_session_resumes,
        session_lock_timeout=session_lock_timeout,
        artifact_verifier=artifact_verifier or artifact_exists,
        session_exists=session_exists,
        session_lock_factory=session_lock_factory,
        result_observer=result_observer,
    )


def _request(
    session_id: str,
    *,
    agent_type: AgentType = "analyst",
    repair_depth: int = 0,
) -> DelegateAgentRequest:
    return DelegateAgentRequest(
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
            DelegateAgentRequest.model_validate(
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

    def test_registry_applies_independent_tool_allowlists(self) -> None:
        definitions = build_agent_definitions(
            [
                search_semantic_resources,
                search_query_experiences,
                execute_sql,
            ],
            [mcp_web_search],
        )

        self.assertEqual(
            definitions["explorer"].tool_names,
            {
                "search_semantic_resources",
                "search_query_experiences",
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

    def test_registry_fails_fast_when_required_tools_are_missing(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少必需的专家工具"):
            build_agent_definitions([search_semantic_resources], [])

    def test_registry_rejects_mcp_tool_names_reserved_by_runtime(self) -> None:
        @tool("execute")
        def conflicting_mcp_tool(command: str) -> str:
            """模拟与 Shell 工具冲突的 MCP 工具"""
            return command

        with self.assertRaisesRegex(ValueError, "冲突"):
            build_agent_definitions(
                [
                    search_semantic_resources,
                    search_query_experiences,
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
        from langgraph.store.memory import InMemoryStore

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
                        store=InMemoryStore(),
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
        from langgraph.store.memory import InMemoryStore

        from app.analytics.agents.planner.agent import create_planner_agent

        @tool
        def delegate_agent(message: str) -> str:
            """委派测试专业 Agent"""
            return message

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
                    delegate_agent=delegate_agent,
                    backend=LocalShellBackend(root_dir=workspace),
                    checkpointer=InMemorySaver(),
                    store=InMemoryStore(),
                    interpreter_mode="thread",
                    interpreter_ptc=["delegate_agent"],
                    interpreter_timeout_seconds=1,
                    interpreter_memory_limit_bytes=2 * 1024 * 1024,
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
        self.assertIn("delegate_agent", model.seen_tools)


class AgentSessionServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证 Session 隔离、并发和修补限制"""

    async def test_registry_builds_and_caches_one_agent_per_session(self) -> None:
        definitions = build_agent_definitions(
            [
                search_semantic_resources,
                search_query_experiences,
                execute_sql,
            ],
            [],
        )
        build_counts: Counter[str] = Counter()

        async def build_agent(
            session_key: AgentSessionKey,
        ) -> CompiledStateGraph:
            build_counts[session_key.checkpoint_ns] += 1
            await asyncio.sleep(0.01)
            return cast(CompiledStateGraph, _FakeAgent())

        registry = AgentRegistry(definitions, build_agent)
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
            registry.get_agent(region),
            registry.get_agent(region),
            registry.get_agent(product),
        )

        self.assertIs(first_region, second_region)
        self.assertIsNot(first_region, product_agent)
        self.assertEqual(build_counts[region.checkpoint_ns], 1)
        self.assertEqual(build_counts[product.checkpoint_ns], 1)

    async def test_completed_result_is_sent_to_result_observer(self) -> None:
        observed: list[tuple[AgentSessionKey, SpecialistResult]] = []

        async def observe(
            session_key: AgentSessionKey,
            result: SpecialistResult,
        ) -> None:
            observed.append((session_key, result))

        service = _service(_FakeAgent(), result_observer=observe)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "observer-run"

        async with service.planner_run("observer-run"):
            result = await service.delegate(
                _request("base", agent_type="explorer"),
                config,
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0].agent_type, "explorer")
        self.assertEqual(observed[0][1].artifacts, result.artifacts)

    async def test_same_session_serializes_while_other_sessions_run_parallel(
        self,
    ) -> None:
        fake = _FakeAgent(delay=0.03)
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "parallel-run"

        async with service.planner_run("parallel-run"):
            results = await asyncio.gather(
                service.delegate(_request("region"), config),
                service.delegate(_request("region"), config),
                service.delegate(_request("product"), config),
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
                service.delegate(_request("region"), config),
                service.delegate(_request("product"), config),
                service.delegate(_request("channel"), config),
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
                first_service.delegate(_request("region"), first_config),
                second_service.delegate(_request("region"), second_config),
            )

        namespace = "subagents/sales-decline/analyst/region"
        self.assertEqual(fake.max_active_by_namespace[namespace], 1)

    async def test_delegate_builds_controlled_subagent_config(self) -> None:
        fake = _FakeAgent()
        service = _service(fake)
        parent = build_planner_config(12, _CONVERSATION_ID)
        parent["metadata"] = {"trace": "kept"}
        parent_configurable = parent.setdefault("configurable", {})
        parent_configurable["checkpoint_id"] = "planner-checkpoint"
        parent_configurable["planner_run_id"] = "config-run"

        async with service.planner_run("config-run"):
            result = await service.delegate(_request("region"), parent)

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
            first = await service.delegate(_request("region"), config)
            second = await service.delegate(_request("region"), config)
            third = await service.delegate(_request("region"), config)

        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertEqual(third.status, "failed")
        self.assertIn("delegation limit", third.limitations[0])

    async def test_session_timeout_returns_failed_protocol_result(self) -> None:
        fake = _FakeAgent(delay=0.05)
        service = _service(fake, session_lock_timeout=0.005)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "timeout-run"

        async with service.planner_run("timeout-run"):
            result = await service.delegate(_request("region"), config)

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
            result = await service.delegate(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("self repair", result.limitations[0])
        self.assertEqual(len(fake.configs), 2)

    async def test_missing_artifact_is_rejected_after_structured_retry(self) -> None:
        fake = _FakeAgent()
        service = _service(fake, artifacts_exist=False)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "artifact-run"

        async with service.planner_run("artifact-run"):
            result = await service.delegate(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("artifact does not exist", result.limitations[0])
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
            result = await service.delegate(_request("region"), config)

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
            result = await service.delegate(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("outside current analysis", result.limitations[0])
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
            result = await service.delegate(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("existing session", result.limitations[0])
        self.assertEqual(len(fake.configs), 2)

    async def test_repair_target_survives_service_restart(self) -> None:
        fake = _FakeAgent()
        first_service = _service(fake)
        first_config = build_planner_config(12, _CONVERSATION_ID)
        first_config.setdefault("configurable", {})["planner_run_id"] = "create-target"
        async with first_service.planner_run("create-target"):
            created = await first_service.delegate(
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
            result = await restarted_service.delegate(
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
        fake.persisted_sessions.add("subagents/sales-decline/explorer/base")
        service = _service(fake)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "signed-depth"

        async with service.planner_run("signed-depth"):
            repair_result = await service.delegate(_request("region"), config)
            wrong_depth = await service.delegate(
                _request("base", agent_type="explorer"),
                config,
            )
            fake.output = None
            repaired = await service.delegate(
                _request("base", agent_type="explorer", repair_depth=1),
                config,
            )
            reset_depth = await service.delegate(
                _request("base", agent_type="explorer"),
                config,
            )
            continued_repair = await service.delegate(
                _request("base", agent_type="explorer", repair_depth=1),
                config,
            )

        self.assertEqual(repair_result.status, "needs_repair")
        self.assertIn("repair depth must be 1", wrong_depth.limitations[0])
        self.assertEqual(repaired.status, "completed")
        self.assertIn("repair depth must be 1", reset_depth.limitations[0])
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
        fake.persisted_sessions.add("subagents/sales-decline/explorer/base")
        service = _service(fake, max_repair_depth=0)
        config = build_planner_config(12, _CONVERSATION_ID)
        config.setdefault("configurable", {})["planner_run_id"] = "repair-depth-run"

        async with service.planner_run("repair-depth-run"):
            result = await service.delegate(_request("region"), config)

        self.assertEqual(result.status, "failed")
        self.assertIn("max repair depth", result.limitations[0])

    async def test_missing_planner_run_id_is_rejected(self) -> None:
        fake = _FakeAgent()
        service = _service(fake)

        result = await service.delegate(
            _request("region"),
            build_planner_config(12, _CONVERSATION_ID),
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("planner_run_id is missing", result.limitations[0])
        self.assertEqual(fake.configs, [])

    async def test_agent_manager_serializes_same_planner_across_workers(self) -> None:
        fake = _FakeAgent()
        first_service = _service(fake)
        second_service = _service(fake)
        graph = cast(CompiledStateGraph, fake)
        distributed_locks = _DistributedLockRegistry()
        first_runtime = ConversationAgentRuntime(
            planner=graph,
            registry=_registry(fake),
            session_service=first_service,
            session_locks=first_service.session_locks,
            parallelism=first_service.parallelism,
            planner_lock=lambda: distributed_locks.acquire("planner"),
            conversation_deleted=_conversation_not_deleted,
        )
        second_runtime = ConversationAgentRuntime(
            planner=graph,
            registry=_registry(fake),
            session_service=second_service,
            session_locks=second_service.session_locks,
            parallelism=second_service.parallelism,
            planner_lock=lambda: distributed_locks.acquire("planner"),
            conversation_deleted=_conversation_not_deleted,
        )
        first_manager = AgentManager(MagicMock())
        second_manager = AgentManager(MagicMock())
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
            registry=_registry(fake),
            session_service=service,
            session_locks=service.session_locks,
            parallelism=service.parallelism,
            planner_lock=lambda: distributed_locks.acquire("conversation"),
            conversation_deleted=conversation_deleted,
        )
        store = MagicMock()

        async def write_tombstone(*args: object, **kwargs: object) -> None:
            nonlocal tombstone
            del args, kwargs
            tombstone = True

        store.aput = AsyncMock(side_effect=write_tombstone)
        persistence = MagicMock()
        persistence.get_store.return_value = store
        persistence.delete_thread = AsyncMock()
        persistence.advisory_lock = lambda *args, **kwargs: distributed_locks.acquire(
            "conversation"
        )
        deleting_worker = AgentManager(persistence)
        serving_worker = AgentManager(MagicMock())

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
    async def test_quickjs_bridge_calls_delegate_with_shared_run_budget(self) -> None:
        from langchain.agents.middleware.types import ModelRequest
        from langchain.tools import ToolRuntime
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.messages import AIMessage
        from langchain_quickjs import CodeInterpreterMiddleware

        from app.analytics.agents.planner.tools import create_delegate_agent_tool

        fake = _FakeAgent()
        service = _service(fake, max_delegations_per_run=1)
        delegate_tool = create_delegate_agent_tool(service)
        middleware = CodeInterpreterMiddleware(
            mode="call",
            ptc=["delegate_agent"],
        )
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([AIMessage(content="ok")])),
            messages=[],
            tools=[delegate_tool],
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
await tools.delegateAgent({
  analysis_id: "sales-decline",
  agent_type: "analyst",
  session_id: "region",
  message: "analyze source",
  repair_depth: 0,
})
""",
                )
        finally:
            middleware._registry.close()

        self.assertIn("completed", str(result.content))
        self.assertEqual(len(fake.configs), 1)
