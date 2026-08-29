"""专业 Agent Session 的持久化委派与并发控制"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock as ThreadLock
from typing import cast
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ValidationError

from app.analytics.agents.contracts import (
    DELEGATION_CONTEXT_KEY,
    DelegationMessageContext,
    DelegationRequest,
    DelegationResult,
    DeleteSessionRequest,
    DeleteSessionResult,
    ListSessionsResult,
    SessionSummary,
    SpecialistResult,
    SubagentActivityWriter,
    SubagentMessageActivity,
    SubagentRunStatus,
    SubagentStatusActivity,
    get_thread_id,
)
from app.analytics.agents.session_store import AgentSessionStore
from app.shared.contracts.analysis import AgentSessionKey, validate_agent_type

_STRUCTURED_RETRY_MESSAGE = """
上一条响应没有通过 SpecialistResult 协议校验。请根据当前 Session 已有工作重新输出结构化结果，不要重复执行工具。
completed 必须包含 findings 和 artifacts；needs_repair 必须包含有 evidence 的 repair_requests；failed 必须包含 limitations。
""".strip()
_INTERNAL_RETRY_KEY = "dataagent_internal_retry"
_STRUCTURED_RESPONSE_TOOL_NAME = "SpecialistResult"


@asynccontextmanager
async def _acquire_nowait(
    guard: asyncio.Lock | asyncio.Semaphore,
    busy_message: str,
) -> AsyncGenerator[None, None]:
    """立即竞争进程内并发许可，已占用时直接失败"""
    if guard.locked():
        raise RuntimeError(busy_message)
    await guard.acquire()
    try:
        yield
    finally:
        guard.release()


@dataclass(slots=True)
class _PlannerRunState:
    """记录单次 Planner 执行的防循环状态"""

    last_repair_fingerprint: dict[str, tuple[object, ...]] = field(default_factory=dict)


class AgentSessionService:
    """绑定一个用户会话并安全调用专业 Agent"""

    def __init__(
        self,
        *,
        build_agent: Callable[[AgentSessionKey], Awaitable[CompiledStateGraph]],
        session_store: AgentSessionStore,
        user_id: int,
        conversation_id: UUID,
        max_parallel_sessions: int,
    ) -> None:
        """初始化会话身份、并发控制和执行限制"""
        if max_parallel_sessions <= 0:
            raise ValueError("max_parallel_sessions 必须为正整数")

        self._build_agent = build_agent
        self._session_store = session_store
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._parallelism = asyncio.Semaphore(max_parallel_sessions)
        self._session_query_parallelism = asyncio.Semaphore(max_parallel_sessions)
        self._active_sessions: dict[str, datetime] = {}
        self._run_states: dict[str, _PlannerRunState] = {}
        self._runtime_state_lock = ThreadLock()

    @asynccontextmanager
    async def planner_run(self, planner_run_id: str) -> AsyncGenerator[None, None]:
        """为单次 Planner 执行建立独立运行状态"""
        with self._runtime_state_lock:
            if planner_run_id in self._run_states:
                raise RuntimeError("Planner 执行状态已存在")
            self._run_states[planner_run_id] = _PlannerRunState()
        try:
            yield
        finally:
            with self._runtime_state_lock:
                self._run_states.pop(planner_run_id, None)

    def _get_run_state(self, parent_config: RunnableConfig) -> _PlannerRunState:
        """通过显式 Planner run ID 读取跨 PTC 边界的共享状态"""
        planner_run_id = parent_config.get("configurable", {}).get("planner_run_id")
        if not isinstance(planner_run_id, str):
            raise TypeError("委派配置中缺少 planner_run_id")
        with self._runtime_state_lock:
            run_state = self._run_states.get(planner_run_id)
        if run_state is None:
            raise RuntimeError("Planner 执行状态不可用")
        return run_state

    async def _is_existing_session(self, session_key: AgentSessionKey) -> bool:
        """从活跃执行或持久化 Checkpoint 识别 Session"""
        with self._runtime_state_lock:
            if session_key.checkpoint_ns in self._active_sessions:
                return True
        return await self._session_store.load_checkpoint(session_key) is not None

    def _parse_session_namespace(self, checkpoint_ns: str) -> AgentSessionKey | None:
        """把受控专业 Session namespace 还原为身份键"""
        parts = checkpoint_ns.split("/")
        if len(parts) != 4 or parts[0] != "subagents":
            return None
        try:
            return AgentSessionKey(
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                analysis_id=parts[1],
                agent_type=validate_agent_type(parts[2]),
                session_id=parts[3],
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _checkpoint_updated_at(checkpoint: Mapping[str, object]) -> datetime | None:
        """读取 Checkpoint 的 UTC 更新时间"""
        timestamp = checkpoint.get("ts")
        if not isinstance(timestamp, str):
            return None
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _failed_result(
        request: DelegationRequest,
        summary: str,
        limitation: str,
    ) -> DelegationResult:
        """构造符合协议的失败结果"""
        return DelegationResult(
            status="failed",
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            summary=summary,
            limitations=[limitation],
        )

    def _build_session_key(
        self,
        analysis_id: str,
        agent_type: str,
        session_id: str,
    ) -> AgentSessionKey:
        """把受控标识绑定到当前用户会话"""
        return AgentSessionKey(
            user_id=self._user_id,
            conversation_id=self._conversation_id,
            analysis_id=analysis_id,
            agent_type=validate_agent_type(agent_type),
            session_id=session_id,
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

    async def list_sessions(self, analysis_id: str | None) -> ListSessionsResult:
        """查询当前 Conversation 内的专业 Agent Session"""
        namespaces = await self._session_store.list_namespaces(analysis_id)
        with self._runtime_state_lock:
            active_sessions = dict(self._active_sessions)
        all_namespaces = set(namespaces)
        for namespace in active_sessions:
            session_key = self._parse_session_namespace(namespace)
            if session_key is not None and (
                analysis_id is None or session_key.analysis_id == analysis_id
            ):
                all_namespaces.add(namespace)

        async def load_summary(checkpoint_ns: str) -> SessionSummary | None:
            """读取单个 Session 的最新持久化状态并叠加活跃状态"""
            session_key = self._parse_session_namespace(checkpoint_ns)
            if session_key is None or (
                analysis_id is not None and session_key.analysis_id != analysis_id
            ):
                return None
            async with self._session_query_parallelism:
                checkpoint = await self._session_store.load_checkpoint(session_key)
            active_at = active_sessions.get(checkpoint_ns)
            if checkpoint is None and active_at is None:
                return None
            status = "interrupted"
            summary: str | None = None
            artifact_count = 0
            updated_at = (
                self._checkpoint_updated_at(checkpoint)
                if checkpoint is not None
                else None
            )
            if checkpoint is not None:
                channel_values = checkpoint.get("channel_values")
                structured_response = (
                    channel_values.get("structured_response")
                    if isinstance(channel_values, Mapping)
                    else None
                )
                try:
                    result = self._parse_specialist_result(structured_response)
                except (TypeError, ValueError, ValidationError):
                    pass
                else:
                    status = result.status
                    summary = result.summary
                    artifact_count = len(result.artifacts)
            if active_at is not None:
                status = "active"
                updated_at = active_at
            return SessionSummary(
                analysis_id=session_key.analysis_id,
                agent_type=session_key.agent_type,
                session_id=session_key.session_id,
                status=status,
                summary=summary,
                artifact_count=artifact_count,
                updated_at=updated_at,
            )

        summaries = await asyncio.gather(
            *(load_summary(namespace) for namespace in sorted(all_namespaces))
        )
        sessions = sorted(
            (summary for summary in summaries if summary is not None),
            key=lambda item: (item.analysis_id, item.agent_type, item.session_id),
        )
        return ListSessionsResult(analysis_id=analysis_id, sessions=sessions)

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

        missing = sorted(await self._session_store.find_missing_files(paths))
        if missing:
            raise ValueError(f"产物不存在: {', '.join(missing)}")

    async def _invoke_specialist(
        self,
        request: DelegationRequest,
        session_key: AgentSessionKey,
        config: RunnableConfig,
        delegation_id: str,
        activity_writer: SubagentActivityWriter | None,
    ) -> SpecialistResult:
        """调用专业 Agent 并允许一次纯结构化修正"""
        agent = await self._build_agent(session_key)
        context = DelegationMessageContext(delegation_id=delegation_id)
        output = await self._stream_specialist(
            agent,
            {
                "messages": [
                    HumanMessage(
                        content=request.message,
                        additional_kwargs={
                            DELEGATION_CONTEXT_KEY: context.model_dump(mode="json")
                        },
                    )
                ]
            },
            config,
            request,
            delegation_id,
            activity_writer,
            emit_messages=True,
        )
        try:
            result = self._parse_specialist_result(output)
            await self._validate_repair_targets(result, session_key)
            await self._verify_result_artifacts(result, session_key)
            return result
        except (TypeError, ValueError, ValidationError):
            retry_output = await self._stream_specialist(
                agent,
                {
                    "messages": [
                        HumanMessage(
                            content=_STRUCTURED_RETRY_MESSAGE,
                            additional_kwargs={_INTERNAL_RETRY_KEY: True},
                        )
                    ]
                },
                config,
                request,
                delegation_id,
                activity_writer,
                emit_messages=False,
            )
            result = self._parse_specialist_result(retry_output)
            await self._validate_repair_targets(result, session_key)
            await self._verify_result_artifacts(result, session_key)
            return result

    @staticmethod
    def _is_public_activity_message(message: BaseMessage) -> bool:
        """筛除结构化协议消息，只保留可展示的 Agent 工作消息"""
        if isinstance(message, AIMessage):
            return not any(
                call.get("name") == _STRUCTURED_RESPONSE_TOOL_NAME
                for call in message.tool_calls
            )
        if isinstance(message, ToolMessage):
            return message.name != _STRUCTURED_RESPONSE_TOOL_NAME
        return False

    async def _stream_specialist(
        self,
        agent: CompiledStateGraph,
        input_state: dict[str, list[HumanMessage]],
        config: RunnableConfig,
        request: DelegationRequest,
        delegation_id: str,
        activity_writer: SubagentActivityWriter | None,
        *,
        emit_messages: bool,
    ) -> Mapping[str, object]:
        """执行 Specialist 并把节点消息投影为当前 Planner 的活动流"""
        final_values: Mapping[str, object] | None = None
        emitted_message_ids: set[str] = set()
        async for part in agent.astream(
            input_state,
            config=config,
            stream_mode=["updates", "values"],
            version="v2",
        ):
            if not isinstance(part, Mapping):
                continue
            part_type = part.get("type")
            data = part.get("data")
            if part_type == "values" and isinstance(data, Mapping):
                final_values = data
                continue
            if (
                not emit_messages
                or activity_writer is None
                or part_type != "updates"
                or not isinstance(data, Mapping)
            ):
                continue
            for node_name, update in data.items():
                if node_name not in {"model", "tools"} or not isinstance(
                    update, Mapping
                ):
                    continue
                messages = update.get("messages")
                if not isinstance(messages, list):
                    continue
                for message in messages:
                    if not isinstance(message, BaseMessage):
                        continue
                    if not self._is_public_activity_message(message):
                        continue
                    if message.id is not None:
                        if message.id in emitted_message_ids:
                            continue
                        emitted_message_ids.add(message.id)
                    activity_writer(
                        SubagentMessageActivity(
                            delegation_id=delegation_id,
                            analysis_id=request.analysis_id,
                            agent_type=request.agent_type,
                            session_id=request.session_id,
                            message=message,
                        )
                    )
        if final_values is None:
            raise RuntimeError("Specialist 执行未产生最终状态")
        return final_values

    async def get_delegation_messages(
        self,
        analysis_id: str,
        agent_type: str,
        session_id: str,
        delegation_id: str,
    ) -> list[BaseMessage] | None:
        """从 Session Checkpoint 读取一次 delegation 的可展示消息"""
        context = DelegationMessageContext(delegation_id=delegation_id)
        session_key = self._build_session_key(analysis_id, agent_type, session_id)
        checkpoint = await self._session_store.load_checkpoint(session_key)
        if checkpoint is None:
            return None
        channel_values = checkpoint.get("channel_values")
        messages = (
            channel_values.get("messages")
            if isinstance(channel_values, Mapping)
            else None
        )
        if not isinstance(messages, list):
            return None

        found = False
        result: list[BaseMessage] = []
        for message in messages:
            if not isinstance(message, BaseMessage):
                continue
            raw_context = message.additional_kwargs.get(DELEGATION_CONTEXT_KEY)
            if raw_context is not None:
                try:
                    message_context = DelegationMessageContext.model_validate(
                        raw_context
                    )
                except ValidationError:
                    if found:
                        break
                    continue
                if found:
                    break
                found = message_context.delegation_id == context.delegation_id
                continue
            if not found:
                continue
            if message.additional_kwargs.get(_INTERNAL_RETRY_KEY) is True:
                break
            if self._is_public_activity_message(message):
                result.append(message)
        return result if found else None

    def _reject_repeated_repair_request(
        self,
        request: DelegationRequest,
        session_key: AgentSessionKey,
        result: SpecialistResult,
        run_state: _PlannerRunState,
    ) -> DelegationResult | None:
        """拒绝同一 Session 连续提出相同的修补请求"""
        if result.status != "needs_repair":
            return None
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
        previous = run_state.last_repair_fingerprint.get(session_key.checkpoint_ns)
        run_state.last_repair_fingerprint[session_key.checkpoint_ns] = fingerprint
        if previous == fingerprint:
            return self._failed_result(
                request,
                "Repeated repair request stopped",
                "the same repair reason and evidence appeared twice consecutively",
            )
        return None

    @staticmethod
    def _to_delegation_result(
        request: DelegationRequest,
        result: SpecialistResult,
    ) -> DelegationResult:
        """补充 Session 身份并生成委派结果"""
        return DelegationResult(
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

    async def execute_delegation(
        self,
        request: DelegationRequest,
        parent_config: RunnableConfig,
        *,
        delegation_id: str | None = None,
        activity_writer: SubagentActivityWriter | None = None,
    ) -> DelegationResult:
        """创建或恢复一个专业 Agent Session"""
        delegation_id = DelegationMessageContext(
            delegation_id=delegation_id or uuid4().hex
        ).delegation_id
        activity_started = False
        try:
            run_state = self._get_run_state(parent_config)
            session_key = self._build_session_key(
                request.analysis_id,
                request.agent_type,
                request.session_id,
            )
            session_lock = self._session_locks.setdefault(
                session_key.checkpoint_ns,
                asyncio.Lock(),
            )
            config = self.build_subagent_config(parent_config, session_key)
            try:
                async with (
                    _acquire_nowait(session_lock, "Session 正在执行或删除"),
                    self._session_store.lock(session_key),
                    _acquire_nowait(
                        self._parallelism,
                        "当前 Conversation 的并行 Session 已满",
                    ),
                ):
                    with self._runtime_state_lock:
                        self._active_sessions[session_key.checkpoint_ns] = datetime.now(
                            UTC
                        )
                    try:
                        if activity_writer is not None:
                            activity_started = True
                            activity_writer(
                                SubagentStatusActivity(
                                    delegation_id=delegation_id,
                                    analysis_id=request.analysis_id,
                                    agent_type=request.agent_type,
                                    session_id=request.session_id,
                                    status="running",
                                )
                            )
                        try:
                            result = await self._invoke_specialist(
                                request,
                                session_key,
                                config,
                                delegation_id,
                                activity_writer,
                            )
                        except Exception:
                            self._write_status_activity(
                                request,
                                delegation_id,
                                "failed",
                                activity_writer,
                            )
                            raise
                    finally:
                        with self._runtime_state_lock:
                            self._active_sessions.pop(
                                session_key.checkpoint_ns,
                                None,
                            )
                    with self._runtime_state_lock:
                        limited_result = self._reject_repeated_repair_request(
                            request,
                            session_key,
                            result,
                            run_state,
                        )
                    if limited_result:
                        self._write_status_activity(
                            request,
                            delegation_id,
                            limited_result.status,
                            activity_writer,
                        )
                        return limited_result
                    self._write_status_activity(
                        request,
                        delegation_id,
                        result.status,
                        activity_writer,
                    )
                    return self._to_delegation_result(request, result)
            except asyncio.CancelledError:
                if activity_started:
                    self._write_status_activity(
                        request,
                        delegation_id,
                        "cancelled",
                        activity_writer,
                    )
                raise
            except Exception as exc:  # noqa: BLE001
                if not activity_started:
                    self._write_status_activity(
                        request,
                        delegation_id,
                        "failed",
                        activity_writer,
                    )
                return self._failed_result(
                    request,
                    "专家智能体会话执行失败",
                    f"{type(exc).__name__}: {exc}",
                )

        except (RuntimeError, TypeError) as exc:
            return self._failed_result(
                request,
                "规划器运行状态拒绝了委派请求",
                str(exc),
            )

    @staticmethod
    def _write_status_activity(
        request: DelegationRequest,
        delegation_id: str,
        status: SubagentRunStatus,
        activity_writer: SubagentActivityWriter | None,
    ) -> None:
        """在存在活动订阅时发送 Specialist 状态"""
        if activity_writer is None:
            return
        activity_writer(
            SubagentStatusActivity(
                delegation_id=delegation_id,
                analysis_id=request.analysis_id,
                agent_type=request.agent_type,
                session_id=request.session_id,
                status=status,
            )
        )

    async def delete_session(
        self,
        request: DeleteSessionRequest,
        parent_config: RunnableConfig,
    ) -> DeleteSessionResult:
        """幂等删除专业 Agent Session 的持久化与沙箱状态"""
        self._get_run_state(parent_config)
        session_key = self._build_session_key(
            request.analysis_id,
            request.agent_type,
            request.session_id,
        )
        session_lock = self._session_locks.setdefault(
            session_key.checkpoint_ns,
            asyncio.Lock(),
        )
        async with (
            _acquire_nowait(session_lock, "Session 正在执行或删除"),
            self._session_store.lock(session_key),
        ):
            try:
                checkpoint_deleted = await self._session_store.delete_checkpoint(
                    session_key
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RuntimeError("删除 Session Checkpoint 失败") from exc
            try:
                workspace_deleted = await self._session_store.delete_workspace(
                    session_key
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RuntimeError("删除 Session 工作区失败") from exc
            with self._runtime_state_lock:
                for run_state in self._run_states.values():
                    run_state.last_repair_fingerprint.pop(
                        session_key.checkpoint_ns,
                        None,
                    )
                self._active_sessions.pop(
                    session_key.checkpoint_ns,
                    None,
                )
        existed = checkpoint_deleted or workspace_deleted
        return DeleteSessionResult(
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            existed=existed,
            message=("Session 已删除" if existed else "Session 不存在，无需删除"),
        )

    def clear(self) -> None:
        """清除无运行任务时的 Session 内存状态"""
        self._session_locks.clear()
        with self._runtime_state_lock:
            self._active_sessions.clear()
            self._run_states.clear()
