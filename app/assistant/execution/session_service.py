"""专业 Agent Session 的持久化委派与并发控制。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from threading import Lock as ThreadLock
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph._internal._constants import CONFIG_KEY_SCRATCHPAD
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.specialists import SpecialistAgentRun
from app.assistant.checkpoints.specialist import (
    SpecialistCheckpointView,
)
from app.assistant.events.stream import MessageDeltaParser
from app.assistant.execution.types import (
    DELEGATION_CONTEXT_KEY,
    DelegationCheckpointRecord,
    DelegationMessageContext,
    DelegationRequest,
    DelegationResult,
    DeleteSessionRequest,
    DeleteSessionResult,
    EvalDelegationRecord,
    ListSessionsResult,
    SessionSummary,
    SubagentActivityWriter,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentRunStatus,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
    get_thread_id,
)
from app.sandbox import SandboxSessionScope
from app.shared.contracts.analysis import AgentSessionKey, validate_agent_type

if TYPE_CHECKING:
    from app.assistant.execution.session_store import PostgresSandboxSessionStore


@asynccontextmanager
async def _acquire_nowait(
    guard: asyncio.Lock | asyncio.Semaphore,
    busy_message: str,
) -> AsyncGenerator[None]:
    """立即竞争进程内并发许可，已占用时直接失败。"""
    if guard.locked():
        raise RuntimeError(busy_message)
    await guard.acquire()
    try:
        yield
    finally:
        guard.release()


class AgentSessionService:
    """绑定一个用户会话并安全调用专业 Agent。"""

    def __init__(
        self,
        *,
        build_agent: Callable[[AgentSessionKey], Awaitable[SpecialistAgentRun]],
        session_store: PostgresSandboxSessionStore,
        user_id: int,
        conversation_id: UUID,
        max_parallel_sessions: int,
        max_sessions: int,
    ) -> None:
        """初始化会话身份、并发控制和执行限制。"""
        if max_parallel_sessions <= 0:
            raise ValueError("max_parallel_sessions 必须为正整数")
        if max_sessions <= 0:
            raise ValueError("max_sessions 必须为正整数")

        self._build_agent = build_agent
        self._session_store = session_store
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._parallelism = asyncio.Semaphore(max_parallel_sessions)
        self._max_sessions = max_sessions
        self._active_sessions: dict[str, datetime] = {}
        self._eval_delegations: dict[str, dict[str, EvalDelegationRecord]] = {}
        self._runtime_state_lock = ThreadLock()

    def begin_eval_delegation(
        self,
        parent_tool_call_id: str,
        delegation_id: str,
        request: DelegationRequest,
    ) -> None:
        """登记一次由 eval 发起、尚未完成的内部委派。"""
        record = EvalDelegationRecord(
            delegation_id=delegation_id,
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            message=request.message,
        )
        with self._runtime_state_lock:
            self._eval_delegations.setdefault(parent_tool_call_id, {})[
                delegation_id
            ] = record

    def is_session_active(self, checkpoint_ns: str) -> bool:
        """返回指定 Session 是否正在当前进程执行。"""
        with self._runtime_state_lock:
            return checkpoint_ns in self._active_sessions

    def finish_eval_delegation(
        self,
        parent_tool_call_id: str,
        delegation_id: str,
        result: DelegationResult,
    ) -> None:
        """把 eval 内部委派的最终结果写入待持久化记录。"""
        with self._runtime_state_lock:
            records = self._eval_delegations.get(parent_tool_call_id)
            if records is None or delegation_id not in records:
                return
            records[delegation_id] = records[delegation_id].model_copy(
                update={"result": result}
            )

    def take_eval_delegations(
        self,
        parent_tool_call_id: str,
    ) -> list[EvalDelegationRecord]:
        """取出并清除一个 eval 收集到的内部委派记录。"""
        with self._runtime_state_lock:
            records = self._eval_delegations.pop(parent_tool_call_id, {})
        return list(records.values())

    def _parse_session_namespace(self, checkpoint_ns: str) -> AgentSessionKey | None:
        """把受控专业 Session namespace 还原为身份键。"""
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
    def _failed_result(
        request: DelegationRequest,
        content: str,
        reason: str,
    ) -> DelegationResult:
        """构造符合协议的失败结果。"""
        return DelegationResult(
            status="failed",
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            content=f"{content}：{reason}",
        )

    def _build_session_key(
        self,
        analysis_id: str,
        agent_type: str,
        session_id: str,
    ) -> AgentSessionKey:
        """把受控标识绑定到当前用户会话。"""
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
        """复制父配置并替换为专业 Session namespace。"""
        config = dict(parent_config)
        # Specialist 使用独立的持久化 namespace，不能继承父任务的子图计数器；
        # 否则 PTC 内部调用会被 LangGraph 自动写入 `<namespace>|N`。
        parent_configurable = {
            key: value
            for key, value in parent_config.get("configurable", {}).items()
            if key
            not in {
                "checkpoint_id",
                "checkpoint_map",
                "checkpoint_ns",
                CONFIG_KEY_SCRATCHPAD,
            }
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
            "workspace_dir": SandboxSessionScope(
                session_key.analysis_id,
                session_key.agent_type,
                session_key.session_id,
            ).workspace_path(session_key.conversation_id),
            "analysis_id": session_key.analysis_id,
            "agent_type": session_key.agent_type,
            "session_id": session_key.session_id,
        }
        return cast(RunnableConfig, config)

    async def list_sessions(self, analysis_id: str | None) -> ListSessionsResult:
        """查询当前 Conversation 内的专业 Agent Session。"""
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
            """读取单个 Session 的最新持久化状态并叠加活跃状态。"""
            session_key = self._parse_session_namespace(checkpoint_ns)
            if session_key is None or (
                analysis_id is not None and session_key.analysis_id != analysis_id
            ):
                return None
            state = await self._session_store.read_state(session_key)
            active_at = active_sessions.get(checkpoint_ns)
            if state.updated_at is None and active_at is None:
                return None
            updated_at = state.updated_at
            if active_at is not None:
                updated_at = active_at
            view = SpecialistCheckpointView(state.values)
            return view.session_summary(
                session_key,
                active=active_at is not None,
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

    async def _invoke_specialist(
        self,
        request: DelegationRequest,
        session_key: AgentSessionKey,
        config: RunnableConfig,
        delegation_id: str,
        activity_writer: SubagentActivityWriter | None,
    ) -> DelegationResult:
        """调用专业 Agent，并记录当前委派的完整文本回答。"""
        agent_run: SpecialistAgentRun | None = None
        try:
            agent_run = await self._build_agent(session_key)
            context = DelegationMessageContext(delegation_id=delegation_id)
            running_record = DelegationCheckpointRecord(
                delegation_id=delegation_id,
                status="running",
            )
            output = await self._stream_specialist(
                agent_run.agent,
                {
                    "messages": [
                        HumanMessage(
                            content=request.message,
                            additional_kwargs={
                                DELEGATION_CONTEXT_KEY: context.model_dump(mode="json")
                            },
                        )
                    ],
                    "delegation_records": {
                        delegation_id: running_record.model_dump(mode="json")
                    },
                },
                config,
                request,
                delegation_id,
                activity_writer,
            )
            content = SpecialistCheckpointView(output).final_response(delegation_id)
            if not content or not content.strip():
                raise RuntimeError("专业 Agent 未输出完整文本回答")
            result = DelegationResult(
                status="completed",
                analysis_id=request.analysis_id,
                agent_type=request.agent_type,
                session_id=request.session_id,
                content=content,
            )
            await self._save_delegation_record(
                agent_run.agent, config, delegation_id, result.status, result
            )
            return result
        except asyncio.CancelledError:
            if agent_run is not None:
                await self._save_delegation_record(
                    agent_run.agent, config, delegation_id, "cancelled"
                )
            raise
        except Exception as exc:
            if agent_run is not None:
                failure = self._failed_result(
                    request, "专家智能体会话执行失败", f"{type(exc).__name__}: {exc}"
                )
                await self._save_delegation_record(
                    agent_run.agent, config, delegation_id, "failed", failure
                )
            raise
        finally:
            if agent_run is not None:
                await self._cleanup_agent_run(agent_run)

    @staticmethod
    async def _save_delegation_record(
        agent: CompiledStateGraph,
        config: RunnableConfig,
        delegation_id: str,
        status: SubagentRunStatus,
        result: DelegationResult | None = None,
    ) -> None:
        """将委派终态按统一结构写入 Checkpoint，保留同 Session 的其他记录。"""
        record = DelegationCheckpointRecord(
            delegation_id=delegation_id, status=status, result=result
        )
        await agent.aupdate_state(
            config,
            {"delegation_records": {delegation_id: record.model_dump(mode="json")}},
        )

    @staticmethod
    async def _cleanup_agent_run(agent_run: SpecialistAgentRun) -> None:
        """屏蔽调用方取消，确保释放 Session 锁前完成 Shell Job 清理。"""
        cleanup_task = asyncio.create_task(agent_run.shell_jobs.cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise

    async def _stream_specialist(
        self,
        agent: CompiledStateGraph,
        input_state: dict[str, object],
        config: RunnableConfig,
        request: DelegationRequest,
        delegation_id: str,
        activity_writer: SubagentActivityWriter | None,
    ) -> Mapping[str, object]:
        """执行 Specialist 并把节点消息投影为当前 Planner 的活动流。"""
        final_values: Mapping[str, object] | None = None
        emitted_message_ids: set[str] = set()
        deltas = MessageDeltaParser()
        # messages 模式提供可实时展示的增量；updates 模式提供节点完成消息；
        # values 模式提供用于提取本轮终答的最终状态。
        async for part in agent.astream(
            input_state,
            config=config,
            stream_mode=["updates", "values", "messages"],
            version="v2",
        ):
            if not isinstance(part, Mapping):
                continue
            part_type = part.get("type")
            data = part.get("data")
            if part_type == "values" and isinstance(data, Mapping):
                final_values = data
                continue
            if activity_writer is not None and part_type == "messages":
                for kind, delta in deltas.parse(data):
                    if not delta["delta"]:
                        continue
                    activity_type = (
                        SubagentThinkingDeltaActivity
                        if kind == "thinking"
                        else SubagentMessageDeltaActivity
                    )
                    activity_writer(
                        activity_type(
                            delegation_id=delegation_id,
                            analysis_id=request.analysis_id,
                            agent_type=request.agent_type,
                            session_id=request.session_id,
                            **delta,
                        )
                    )
                continue
            if (
                activity_writer is None
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
                    if not isinstance(message, AIMessage | ToolMessage):
                        continue
                    public_message = message
                    if public_message.id is not None:
                        if public_message.id in emitted_message_ids:
                            continue
                        emitted_message_ids.add(public_message.id)
                    activity_writer(
                        SubagentMessageActivity(
                            delegation_id=delegation_id,
                            analysis_id=request.analysis_id,
                            agent_type=request.agent_type,
                            session_id=request.session_id,
                            message=public_message,
                        )
                    )
        if final_values is None:
            raise RuntimeError("Specialist 执行未产生最终状态")
        return final_values

    async def _get_replayed_delegation_result(
        self,
        request: DelegationRequest,
        session_key: AgentSessionKey,
        delegation_id: str,
    ) -> DelegationResult | None:
        """恢复 Planner 待执行工具时复用同一 delegation 的既有结果。"""
        state_values = (await self._session_store.read_state(session_key)).values
        return SpecialistCheckpointView(state_values).replayed_result(
            request,
            delegation_id,
        )

    async def execute_delegation(
        self,
        request: DelegationRequest,
        parent_config: RunnableConfig,
        *,
        delegation_id: str | None = None,
        activity_writer: SubagentActivityWriter | None = None,
    ) -> DelegationResult:
        """创建或恢复一个专业 Agent Session。"""
        delegation_id = DelegationMessageContext(
            delegation_id=delegation_id or uuid4().hex
        ).delegation_id
        activity_started = False
        session_key = self._build_session_key(
            request.analysis_id,
            request.agent_type,
            request.session_id,
        )
        config = self.build_subagent_config(parent_config, session_key)
        try:
            async with (
                self._session_store.lock(session_key),
                self._session_store.reserve_capacity(session_key, self._max_sessions),
                _acquire_nowait(
                    self._parallelism,
                    "当前 Conversation 的并行 Session 已满",
                ),
            ):
                replayed_result = await self._get_replayed_delegation_result(
                    request,
                    session_key,
                    delegation_id,
                )
                if replayed_result is not None:
                    self._write_status_activity(
                        request,
                        delegation_id,
                        replayed_result.status,
                        activity_writer,
                    )
                    return replayed_result
                with self._runtime_state_lock:
                    self._active_sessions[session_key.checkpoint_ns] = datetime.now(UTC)
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
                self._write_status_activity(
                    request,
                    delegation_id,
                    result.status,
                    activity_writer,
                )
                return result
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

    @staticmethod
    def _write_status_activity(
        request: DelegationRequest,
        delegation_id: str,
        status: SubagentRunStatus,
        activity_writer: SubagentActivityWriter | None,
    ) -> None:
        """在存在活动订阅时发送 Specialist 状态。"""
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
    ) -> DeleteSessionResult:
        """幂等删除专业 Agent Session 的持久化与沙箱状态。"""
        session_key = self._build_session_key(
            request.analysis_id,
            request.agent_type,
            request.session_id,
        )
        async with (
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
        """清除无运行任务时的 Session 内存状态。"""
        with self._runtime_state_lock:
            self._active_sessions.clear()
            self._eval_delegations.clear()
