"""专业 Agent Session 的持久化委派与并发控制。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import Context, copy_context
from datetime import UTC, datetime
from functools import partial
from threading import Lock as ThreadLock
from typing import cast
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.assistant.checkpoints.specialist import SpecialistCheckpointView
from app.assistant.events.stream import MessageDeltaParser, update_messages
from app.assistant.execution.session_store import PostgresSandboxSessionStore
from app.assistant.execution.types import (
    DELEGATION_CONTEXT_KEY,
    DelegationCheckpointRecord,
    DelegationMessageContext,
    DelegationRequest,
    DelegationResult,
    DeleteSessionRequest,
    DeleteSessionResult,
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
from app.sandbox.paths import SandboxSessionScope
from app.shared.contracts.analysis import AgentSessionKey, validate_agent_type


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
        build_agent: Callable[[AgentSessionKey], Awaitable[CompiledStateGraph]],
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
        self._runtime_state_lock = ThreadLock()

    def is_session_active(self, thread_id: str) -> bool:
        """返回指定 Session 是否正在当前进程执行。"""
        with self._runtime_state_lock:
            return thread_id in self._active_sessions

    def _parse_session_thread(self, thread_id: str) -> AgentSessionKey | None:
        """把受控专业 Session 线程还原为身份键。"""
        prefix = f"{get_thread_id(self._user_id, self._conversation_id)}/subagents/"
        if not thread_id.startswith(prefix):
            return None
        parts = thread_id.removeprefix(prefix).split("/")
        if len(parts) != 3:
            return None
        try:
            return AgentSessionKey(
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                analysis_id=parts[0],
                agent_type=validate_agent_type(parts[1]),
                session_id=parts[2],
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def build_subagent_config(
        parent_config: RunnableConfig,
        session_key: AgentSessionKey,
    ) -> RunnableConfig:
        """传递标签、元数据与递归限制，使用 Session 的独立线程。"""
        config = {
            key: value
            for key, value in parent_config.items()
            if key in {"tags", "metadata", "recursion_limit"}
        }
        config["configurable"] = {
            "thread_id": session_key.thread_id,
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
        threads = await self._session_store.list_threads(analysis_id)
        with self._runtime_state_lock:
            active_sessions = dict(self._active_sessions)
        all_threads = set(threads)
        for thread in active_sessions:
            session_key = self._parse_session_thread(thread)
            if session_key is not None and (
                analysis_id is None or session_key.analysis_id == analysis_id
            ):
                all_threads.add(thread)

        async def load_summary(thread_id: str) -> SessionSummary | None:
            """读取单个 Session 的最新持久化状态并叠加活跃状态。"""
            session_key = self._parse_session_thread(thread_id)
            if session_key is None or (
                analysis_id is not None and session_key.analysis_id != analysis_id
            ):
                return None
            state = await self._session_store.read_state(session_key)
            active_at = active_sessions.get(thread_id)
            if state.created_at is None and active_at is None:
                return None
            updated_at = (
                datetime.fromisoformat(state.created_at) if state.created_at else None
            )
            if active_at is not None:
                updated_at = active_at
            view = SpecialistCheckpointView(state.values)
            return view.session_summary(
                session_key,
                active=active_at is not None,
                updated_at=updated_at,
            )

        summaries = await asyncio.gather(
            *(load_summary(thread) for thread in sorted(all_threads))
        )
        sessions = sorted(
            (summary for summary in summaries if summary is not None),
            key=lambda item: (item.analysis_id, item.agent_type, item.session_id),
        )
        return ListSessionsResult(analysis_id=analysis_id, sessions=sessions)

    async def _invoke_specialist(
        self,
        request: DelegationRequest,
        agent: CompiledStateGraph,
        config: RunnableConfig,
        delegation_id: str,
        activity_writer: SubagentActivityWriter | None,
    ) -> str:
        """执行专业 Agent，并保存本次委派的最终文本。"""
        try:
            context = DelegationMessageContext(delegation_id=delegation_id)
            record = DelegationCheckpointRecord(
                delegation_id=delegation_id, status="running"
            )
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
                    ],
                    "delegation_records": {
                        delegation_id: record.model_dump(mode="json")
                    },
                },
                config,
                request,
                delegation_id,
                activity_writer,
            )
            result = SpecialistCheckpointView(output).plain_response(delegation_id)
            if result is None:
                raise RuntimeError("专业 Agent 未返回最终文本")
            await self._save_delegation_record(
                agent, config, delegation_id, "completed", result
            )
            return result
        except asyncio.CancelledError:
            await self._save_delegation_record(
                agent, config, delegation_id, "cancelled"
            )
            raise
        except Exception as exc:
            await self._save_delegation_record(
                agent,
                config,
                delegation_id,
                "failed",
                f"专业 Agent 执行失败: {type(exc).__name__}: {exc}",
            )
            raise

    @staticmethod
    async def _save_delegation_record(
        agent: CompiledStateGraph,
        config: RunnableConfig,
        delegation_id: str,
        status: SubagentRunStatus,
        result: str | None = None,
    ) -> None:
        """将委派终态按统一结构写入 Checkpoint，保留同 Session 的其他记录。"""
        record = DelegationCheckpointRecord(
            delegation_id=delegation_id, status=status, result=result
        )
        await agent.aupdate_state(
            config,
            {"delegation_records": {delegation_id: record.model_dump(mode="json")}},
        )

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
        # 从增量、节点消息和最终状态分别读取流式内容、完整消息和委派结果。
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
            for message in update_messages(data):
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

    async def execute_delegation(
        self,
        request: DelegationRequest,
        parent_config: RunnableConfig,
        *,
        delegation_id: str | None = None,
        activity_writer: SubagentActivityWriter | None = None,
    ) -> DelegationResult:
        """在独立图上下文中运行 Session，隔离父图的调度与检查点配置。"""
        return await asyncio.create_task(
            self._execute_delegation(
                request,
                parent_config,
                delegation_id=delegation_id,
                activity_writer=(
                    partial(copy_context().run, activity_writer)
                    if activity_writer is not None
                    else None
                ),
            ),
            context=Context(),
        )

    async def _execute_delegation(
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
        session_key = AgentSessionKey(
            user_id=self._user_id,
            conversation_id=self._conversation_id,
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
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
                agent = await self._build_agent(session_key)
                state = await agent.aget_state(config)
                replayed_result = SpecialistCheckpointView(
                    state.values
                ).replayed_result(request, delegation_id)
                if replayed_result is not None:
                    self._write_status_activity(
                        request,
                        delegation_id,
                        replayed_result.status,
                        activity_writer,
                    )
                    return replayed_result
                with self._runtime_state_lock:
                    self._active_sessions[session_key.thread_id] = datetime.now(UTC)
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
                            agent,
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
                            session_key.thread_id,
                            None,
                        )
                self._write_status_activity(
                    request,
                    delegation_id,
                    "completed",
                    activity_writer,
                )
                return DelegationResult(
                    status="completed",
                    content=result,
                    analysis_id=request.analysis_id,
                    agent_type=request.agent_type,
                    session_id=request.session_id,
                )
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
            return DelegationResult(
                status="failed",
                analysis_id=request.analysis_id,
                agent_type=request.agent_type,
                session_id=request.session_id,
                content=f"专家智能体会话执行失败: {type(exc).__name__}: {exc}",
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
        session_key = AgentSessionKey(
            user_id=self._user_id,
            conversation_id=self._conversation_id,
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
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
                    session_key.thread_id,
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
