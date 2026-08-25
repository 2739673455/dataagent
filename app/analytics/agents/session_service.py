"""专业 Agent Session 的持久化委派与并发控制"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from threading import Lock as ThreadLock
from typing import cast
from uuid import UUID

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, ValidationError

from app.analytics.agents.contracts import (
    DelegateAgentRequest,
    DelegateAgentResult,
    SpecialistResult,
    get_thread_id,
)
from app.analytics.agents.registry import AgentRegistry
from app.shared.contracts.analysis import AgentSessionKey

_STRUCTURED_RETRY_MESSAGE = """
上一条响应没有通过 SpecialistResult 协议校验。请根据当前 Session 已有工作重新输出结构化结果，不要重复执行工具。
completed 必须包含 findings 和 artifacts；needs_repair 必须包含有 evidence 的 repair_requests；failed 必须包含 limitations。
""".strip()
_MAX_PARALLEL_ARTIFACT_VERIFICATIONS = 8

type SpecialistResultObserver = Callable[
    [AgentSessionKey, SpecialistResult],
    Awaitable[None],
]


@dataclass(slots=True)
class _ExecutionBudget:
    """记录单次 Planner 执行的委派与修补预算"""

    delegations: int = 0
    session_resumes: Counter[str] = field(default_factory=Counter)
    repair_rounds: Counter[str] = field(default_factory=Counter)
    last_repair_fingerprint: dict[str, tuple[object, ...]] = field(default_factory=dict)
    pending_repair_depths: dict[str, int] = field(default_factory=dict)
    session_repair_depths: dict[str, int] = field(default_factory=dict)


class AgentSessionService:
    """绑定一个用户会话并安全调用专业 Agent"""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        user_id: int,
        conversation_id: UUID,
        max_parallel_sessions: int,
        max_delegations_per_run: int,
        max_repair_rounds: int,
        max_repair_depth: int,
        max_session_resumes: int,
        session_lock_timeout: float,
        artifact_verifier: Callable[[str], Awaitable[bool]],
        session_exists: Callable[[AgentSessionKey], Awaitable[bool]],
        session_lock_factory: Callable[
            [AgentSessionKey],
            AbstractAsyncContextManager[None],
        ],
        result_observer: SpecialistResultObserver | None = None,
    ) -> None:
        """初始化会话身份、并发控制和委派预算限制"""
        if max_parallel_sessions <= 0:
            raise ValueError("max_parallel_sessions 必须为正整数")
        if max_delegations_per_run <= 0:
            raise ValueError("max_delegations_per_run 必须为正整数")
        if max_repair_rounds < 0 or max_repair_depth < 0:
            raise ValueError("修补限制参数不能为负数")
        if max_session_resumes <= 0:
            raise ValueError("max_session_resumes 必须为正整数")
        if session_lock_timeout <= 0:
            raise ValueError("session_lock_timeout 必须为正数")

        self._registry = registry
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._max_delegations_per_run = max_delegations_per_run
        self._max_repair_rounds = max_repair_rounds
        self._max_repair_depth = max_repair_depth
        self._max_session_resumes = max_session_resumes
        self._session_lock_timeout = session_lock_timeout
        self._artifact_verifier = artifact_verifier
        self._artifact_verification_parallelism = asyncio.Semaphore(
            min(max_parallel_sessions, _MAX_PARALLEL_ARTIFACT_VERIFICATIONS)
        )
        self._session_exists = session_exists
        self._session_lock_factory = session_lock_factory
        self._result_observer = result_observer
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._parallelism = asyncio.Semaphore(max_parallel_sessions)
        self._known_sessions: set[str] = set()
        self._budgets: dict[str, _ExecutionBudget] = {}
        self._budget_lock = ThreadLock()

    @property
    def session_locks(self) -> Mapping[str, asyncio.Lock]:
        """返回只读语义的 Session 锁视图"""
        return self._session_locks

    @property
    def parallelism(self) -> asyncio.Semaphore:
        """返回会话级并发控制器"""
        return self._parallelism

    @asynccontextmanager
    async def planner_run(self, planner_run_id: str) -> AsyncGenerator[None, None]:
        """为单次 Planner 执行建立独立委派预算"""
        with self._budget_lock:
            if planner_run_id in self._budgets:
                raise RuntimeError("Planner 执行预算已存在")
            self._budgets[planner_run_id] = _ExecutionBudget()
        try:
            yield
        finally:
            with self._budget_lock:
                self._budgets.pop(planner_run_id, None)

    def _get_budget(self, parent_config: RunnableConfig) -> _ExecutionBudget:
        """通过显式 Planner run ID 读取跨 PTC 边界的共享预算"""
        planner_run_id = parent_config.get("configurable", {}).get("planner_run_id")
        if not isinstance(planner_run_id, str):
            raise TypeError("委派配置中缺少 planner_run_id")
        with self._budget_lock:
            budget = self._budgets.get(planner_run_id)
        if budget is None:
            raise RuntimeError("Planner 执行预算不可用")
        return budget

    def _consume_delegation(
        self,
        budget: _ExecutionBudget,
        session_key: AgentSessionKey,
        persisted_session_exists: bool,
    ) -> str | None:
        """原子检查委派和 Session 恢复次数"""
        budget.delegations += 1
        if budget.delegations > self._max_delegations_per_run:
            return "本次 Planner 执行已达到委派次数上限"

        checkpoint_ns = session_key.checkpoint_ns
        if persisted_session_exists or checkpoint_ns in self._known_sessions:
            budget.session_resumes[checkpoint_ns] += 1
            if budget.session_resumes[checkpoint_ns] > self._max_session_resumes:
                return "本次 Planner 执行已达到 Session 续接次数上限"
        else:
            self._known_sessions.add(checkpoint_ns)
        return None

    async def _is_existing_session(self, session_key: AgentSessionKey) -> bool:
        """从当前进程缓存或持久化 Checkpoint 识别 Session"""
        with self._budget_lock:
            if session_key.checkpoint_ns in self._known_sessions:
                return True
        exists = await self._session_exists(session_key)
        if exists:
            with self._budget_lock:
                self._known_sessions.add(session_key.checkpoint_ns)
        return exists

    @staticmethod
    def _failed_result(
        request: DelegateAgentRequest,
        summary: str,
        limitation: str,
    ) -> DelegateAgentResult:
        """构造符合协议的失败结果"""
        return DelegateAgentResult(
            status="failed",
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            summary=summary,
            limitations=[limitation],
        )

    def _build_session_key(self, request: DelegateAgentRequest) -> AgentSessionKey:
        """把已校验请求绑定到当前用户会话"""
        return AgentSessionKey(
            user_id=self._user_id,
            conversation_id=self._conversation_id,
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
        )

    @staticmethod
    def build_subagent_config(
        parent_config: RunnableConfig,
        session_key: AgentSessionKey,
    ) -> RunnableConfig:
        """复制父配置并替换为专业 Session namespace"""
        config = dict(parent_config)
        parent_configurable = {
            key: value
            for key, value in parent_config.get("configurable", {}).items()
            if key not in {"checkpoint_id", "checkpoint_map", "checkpoint_ns"}
        }
        config["configurable"] = {
            **parent_configurable,
            "thread_id": get_thread_id(
                session_key.user_id,
                session_key.conversation_id,
            ),
            "checkpoint_ns": session_key.checkpoint_ns,
            "user_id": session_key.user_id,
            "conversation_id": str(session_key.conversation_id),
            "workspace_dir": session_key.workspace_dir,
            "analysis_id": session_key.analysis_id,
            "agent_type": session_key.agent_type,
            "session_id": session_key.session_id,
        }
        return cast(RunnableConfig, config)

    @staticmethod
    def _parse_specialist_result(output: object) -> SpecialistResult:
        """从 LangGraph 输出提取并严格校验结构化结果"""
        candidate: object = output
        if isinstance(output, Mapping) and "structured_response" in output:
            candidate = output["structured_response"]
        if isinstance(candidate, SpecialistResult):
            return candidate
        if isinstance(candidate, BaseModel):
            candidate = candidate.model_dump(mode="python")
        return SpecialistResult.model_validate(candidate)

    async def _validate_repair_targets(
        self,
        result: SpecialistResult,
        session_key: AgentSessionKey,
    ) -> None:
        """只允许修补同 Analysis 内已存在的其他 Session"""
        for request in result.repair_requests:
            target_key = AgentSessionKey(
                user_id=session_key.user_id,
                conversation_id=session_key.conversation_id,
                analysis_id=session_key.analysis_id,
                agent_type=request.target_agent_type,
                session_id=request.target_session_id,
            )
            if target_key.checkpoint_ns == session_key.checkpoint_ns:
                raise ValueError("专业 Agent Session 不能请求修补自身")
            if not await self._is_existing_session(target_key):
                raise ValueError(
                    "修补目标必须是同一分析中已存在的 Session: "
                    f"{request.target_agent_type}/{request.target_session_id}"
                )

    async def _verify_result_artifacts(
        self,
        result: SpecialistResult,
        session_key: AgentSessionKey,
    ) -> None:
        """验证结论产物和修补证据实际存在于当前工作区"""
        session_prefix = (
            f"/analyses/{session_key.analysis_id}/sessions/{session_key.agent_type}/"
            f"{session_key.session_id}/"
        )
        shared_prefix = f"/analyses/{session_key.analysis_id}/shared/"
        analysis_prefix = f"/analyses/{session_key.analysis_id}/"
        artifact_paths = {artifact.path for artifact in result.artifacts}
        invalid_artifacts = sorted(
            path
            for path in artifact_paths
            if not path.startswith((session_prefix, shared_prefix))
        )
        evidence_paths = {
            artifact.path
            for repair in result.repair_requests
            for artifact in repair.evidence
        }
        invalid_evidence = sorted(
            path for path in evidence_paths if not path.startswith(analysis_prefix)
        )
        if invalid_artifacts or invalid_evidence:
            invalid = [*invalid_artifacts, *invalid_evidence]
            raise ValueError(f"产物路径超出当前分析范围: {', '.join(invalid)}")
        paths = artifact_paths | evidence_paths

        async def verify(path: str) -> bool:
            """在受控并发范围内验证单个产物路径"""
            async with self._artifact_verification_parallelism:
                return await self._artifact_verifier(path)

        verified = await asyncio.gather(*(verify(path) for path in sorted(paths)))
        missing = [
            path
            for path, exists in zip(sorted(paths), verified, strict=True)
            if not exists
        ]
        if missing:
            raise ValueError(f"产物不存在: {', '.join(missing)}")

    async def _invoke_specialist(
        self,
        request: DelegateAgentRequest,
        session_key: AgentSessionKey,
        config: RunnableConfig,
    ) -> SpecialistResult:
        """调用专业 Agent 并允许一次纯结构化修正"""
        agent = await self._registry.get_agent(session_key)
        output = await agent.ainvoke(
            {"messages": [HumanMessage(content=request.message)]},
            config=config,
        )
        try:
            result = self._parse_specialist_result(output)
            await self._validate_repair_targets(result, session_key)
            await self._verify_result_artifacts(result, session_key)
            return result
        except (TypeError, ValueError, ValidationError):
            retry_output = await agent.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=_STRUCTURED_RETRY_MESSAGE,
                            additional_kwargs={"dataagent_internal_retry": True},
                        )
                    ]
                },
                config=config,
            )
            result = self._parse_specialist_result(retry_output)
            await self._validate_repair_targets(result, session_key)
            await self._verify_result_artifacts(result, session_key)
            return result

    def _apply_repair_limits(
        self,
        request: DelegateAgentRequest,
        session_key: AgentSessionKey,
        result: SpecialistResult,
        budget: _ExecutionBudget,
    ) -> DelegateAgentResult | None:
        """检查修补轮次、深度和重复原因"""
        if result.status != "needs_repair":
            return None
        if request.repair_depth >= self._max_repair_depth:
            return self._failed_result(
                request,
                "Repair request stopped at the configured depth limit",
                f"max repair depth is {self._max_repair_depth}",
            )

        budget.repair_rounds[request.analysis_id] += 1
        if budget.repair_rounds[request.analysis_id] > self._max_repair_rounds:
            return self._failed_result(
                request,
                "Repair request stopped at the configured round limit",
                f"max repair rounds is {self._max_repair_rounds}",
            )

        fingerprint = tuple(
            sorted(
                (
                    repair.target_agent_type,
                    repair.target_session_id,
                    repair.reason,
                    tuple(artifact.path for artifact in repair.evidence),
                )
                for repair in result.repair_requests
            )
        )
        previous = budget.last_repair_fingerprint.get(session_key.checkpoint_ns)
        budget.last_repair_fingerprint[session_key.checkpoint_ns] = fingerprint
        if previous == fingerprint:
            return self._failed_result(
                request,
                "Repeated repair request stopped",
                "the same repair reason and evidence appeared twice consecutively",
            )
        next_depth = request.repair_depth + 1
        for repair in result.repair_requests:
            target_key = AgentSessionKey(
                user_id=session_key.user_id,
                conversation_id=session_key.conversation_id,
                analysis_id=session_key.analysis_id,
                agent_type=repair.target_agent_type,
                session_id=repair.target_session_id,
            )
            previous_depth = budget.session_repair_depths.get(
                target_key.checkpoint_ns,
                0,
            )
            pending_depth = budget.pending_repair_depths.get(
                target_key.checkpoint_ns,
                0,
            )
            budget.pending_repair_depths[target_key.checkpoint_ns] = max(
                next_depth,
                previous_depth,
                pending_depth,
            )
        return None

    @staticmethod
    def _validate_repair_depth(
        request: DelegateAgentRequest,
        session_key: AgentSessionKey,
        budget: _ExecutionBudget,
    ) -> str | None:
        """校验服务端为修补目标签发的深度"""
        checkpoint_ns = session_key.checkpoint_ns
        expected_depth = budget.pending_repair_depths.get(
            checkpoint_ns,
            budget.session_repair_depths.get(checkpoint_ns, 0),
        )
        if request.repair_depth != expected_depth:
            return f"当前 Session 的修补深度必须为 {expected_depth}"
        return None

    @staticmethod
    def _consume_repair_depth(
        request: DelegateAgentRequest,
        session_key: AgentSessionKey,
        budget: _ExecutionBudget,
    ) -> None:
        """在委派预算通过后消费匹配的修补深度"""
        expected_depth = budget.pending_repair_depths.get(session_key.checkpoint_ns)
        if expected_depth == request.repair_depth:
            del budget.pending_repair_depths[session_key.checkpoint_ns]
        budget.session_repair_depths[session_key.checkpoint_ns] = request.repair_depth

    @staticmethod
    def _to_delegate_result(
        request: DelegateAgentRequest,
        result: SpecialistResult,
    ) -> DelegateAgentResult:
        """补充 Session 身份并生成委派结果"""
        return DelegateAgentResult(
            status=result.status,
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            summary=result.summary,
            findings=result.findings,
            artifacts=result.artifacts,
            repair_requests=result.repair_requests,
            confidence=result.confidence,
            limitations=result.limitations,
        )

    async def delegate(
        self,
        request: DelegateAgentRequest,
        parent_config: RunnableConfig,
    ) -> DelegateAgentResult:
        """创建或恢复一个专业 Agent Session"""
        try:
            budget = self._get_budget(parent_config)
            session_key = self._build_session_key(request)
            if request.repair_depth > self._max_repair_depth:
                return self._failed_result(
                    request,
                    "Delegation rejected by repair depth limit",
                    f"max repair depth is {self._max_repair_depth}",
                )
            persisted_session_exists = await self._is_existing_session(session_key)
            with self._budget_lock:
                limitation = self._validate_repair_depth(
                    request,
                    session_key,
                    budget,
                )
                if limitation is None:
                    limitation = self._consume_delegation(
                        budget,
                        session_key,
                        persisted_session_exists,
                    )
                if limitation is None:
                    self._consume_repair_depth(request, session_key, budget)
            if limitation:
                return self._failed_result(
                    request,
                    "Delegation rejected by execution limit",
                    limitation,
                )

            session_lock = self._session_locks.setdefault(
                session_key.checkpoint_ns,
                asyncio.Lock(),
            )
            config = self.build_subagent_config(parent_config, session_key)
            try:
                async with asyncio.timeout(self._session_lock_timeout):
                    async with session_lock:
                        async with self._session_lock_factory(session_key):
                            async with self._parallelism:
                                result = await self._invoke_specialist(
                                    request,
                                    session_key,
                                    config,
                                )
            except TimeoutError:
                return self._failed_result(
                    request,
                    "专家智能体会话超时",
                    f"超时时间为 {self._session_lock_timeout} 秒",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                return self._failed_result(
                    request,
                    "专家智能体会话执行失败",
                    f"{type(exc).__name__}: {exc}",
                )

            if self._result_observer is not None and result.status == "completed":
                try:
                    await self._result_observer(session_key, result)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        f"专家执行结果观察器处理失败: checkpoint_ns={session_key.checkpoint_ns}"
                    )

            with self._budget_lock:
                limited_result = self._apply_repair_limits(
                    request,
                    session_key,
                    result,
                    budget,
                )
            if limited_result:
                return limited_result
            return self._to_delegate_result(request, result)
        except (RuntimeError, TypeError) as exc:
            return self._failed_result(
                request,
                "规划器运行状态拒绝了委派请求",
                str(exc),
            )

    def clear(self) -> None:
        """清除无运行任务时的 Session 内存状态"""
        self._session_locks.clear()
        self._known_sessions.clear()
        with self._budget_lock:
            self._budgets.clear()
