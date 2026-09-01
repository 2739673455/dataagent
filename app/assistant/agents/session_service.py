"""专业 Agent Session 的持久化委派与并发控制。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from threading import Lock as ThreadLock
from typing import cast
from uuid import UUID, uuid4

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.constants import CONFIG_KEY_CHECKPOINTER
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ValidationError

from app.assistant.agents.contracts import (
    DELEGATION_CONTEXT_KEY,
    MESSAGE_CREATED_AT_KEY,
    DelegationActivityHistory,
    DelegationMessageContext,
    DelegationRequest,
    DelegationResult,
    DeleteSessionRequest,
    DeleteSessionResult,
    EvalDelegationRecord,
    ListSessionsResult,
    SessionSummary,
    SpecialistResult,
    SubagentActivityWriter,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentRunStatus,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
    get_thread_id,
)
from app.assistant.agents.session_store import AgentSessionStore
from app.assistant.agents.specialists import SpecialistAgentRun
from app.assistant.message_content import reasoning_text
from app.sandbox.paths import SandboxSessionScope, resolve_sandbox_path
from app.shared.contracts.analysis import AgentSessionKey, validate_agent_type

_STRUCTURED_RETRY_MESSAGE = """
上一条响应没有通过 SpecialistResult 协议校验。请根据当前 Session 已有工作重新输出结构化结果，不要重复执行工具。
completed 必须在 content 中给出完整结论；needs_repair 必须包含 repair_requests；failed 必须包含 failure_reasons。
""".strip()
_INTERNAL_RETRY_KEY = "dataagent_internal_retry"
_STRUCTURED_RESPONSE_TOOL_NAME = "SpecialistResult"


def _session_workspace(session_key: AgentSessionKey) -> str:
    """返回 Session 在容器中的规范工作目录。"""
    return SandboxSessionScope(
        session_key.analysis_id,
        session_key.agent_type,
        session_key.session_id,
    ).workspace_path(session_key.conversation_id)


@asynccontextmanager
async def _acquire_nowait(
    guard: asyncio.Lock | asyncio.Semaphore,
    busy_message: str,
) -> AsyncGenerator[None, None]:
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
        session_store: AgentSessionStore,
        user_id: int,
        conversation_id: UUID,
        max_parallel_sessions: int,
    ) -> None:
        """初始化会话身份、并发控制和执行限制。"""
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

    async def _is_existing_session(self, session_key: AgentSessionKey) -> bool:
        """从活跃执行或持久化 Checkpoint 识别 Session。"""
        with self._runtime_state_lock:
            if session_key.checkpoint_ns in self._active_sessions:
                return True
        return await self._session_store.load_checkpoint(session_key) is not None

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
    def _checkpoint_updated_at(checkpoint: Mapping[str, object]) -> datetime | None:
        """读取 Checkpoint 的 UTC 更新时间。"""
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
        content: str,
        reason: str,
    ) -> DelegationResult:
        """构造符合协议的失败结果。"""
        return DelegationResult(
            status="failed",
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            content=content,
            failure_reasons=[reason],
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
            "workspace_dir": _session_workspace(session_key),
            "analysis_id": session_key.analysis_id,
            "agent_type": session_key.agent_type,
            "session_id": session_key.session_id,
        }
        return cast(RunnableConfig, config)

    @staticmethod
    def _parse_specialist_result(output: object) -> SpecialistResult:
        """从 LangGraph 输出提取并严格校验结构化结果。"""
        candidate: object = output
        if isinstance(output, Mapping) and "structured_response" in output:
            candidate = output["structured_response"]
        if isinstance(candidate, SpecialistResult):
            return candidate
        if isinstance(candidate, BaseModel):
            candidate = candidate.model_dump(mode="python")
        return SpecialistResult.model_validate(candidate)

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
                    summary = result.content
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
        """只允许修补同 Analysis 内已存在的其他 Session。"""
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
        """验证结论产物实际存在于当前工作区。"""
        session_prefix = f"{_session_workspace(session_key)}/"
        artifact_paths = {artifact.path for artifact in result.artifacts}
        invalid_artifacts = sorted(
            path for path in artifact_paths if not path.startswith(session_prefix)
        )
        if invalid_artifacts:
            raise ValueError(
                f"产物路径超出当前 Session: {', '.join(invalid_artifacts)}"
            )
        paths = artifact_paths

        missing = sorted(await self._session_store.find_missing_files(paths))
        if missing:
            raise ValueError(f"产物不存在: {', '.join(missing)}")

    @staticmethod
    def _resolve_result_artifacts(
        result: SpecialistResult,
        session_key: AgentSessionKey,
    ) -> SpecialistResult:
        """以产出该文件的 Session 为基准解析相对产物路径。"""
        workspace = _session_workspace(session_key)
        artifacts = [
            artifact.model_copy(
                update={"path": resolve_sandbox_path(artifact.path, workspace)}
            )
            for artifact in result.artifacts
        ]
        return result.model_copy(update={"artifacts": artifacts})

    async def _prepare_specialist_result(
        self,
        result: SpecialistResult,
        session_key: AgentSessionKey,
    ) -> SpecialistResult:
        """规范化并校验即将跨 Agent 传递的结构化结果。"""
        # 相对路径只在产出它的 Session 内有明确含义；跨过此边界后统一传递绝对路径。
        result = self._resolve_result_artifacts(result, session_key)
        await self._validate_repair_targets(result, session_key)
        await self._verify_result_artifacts(result, session_key)
        return result

    async def _invoke_specialist(
        self,
        request: DelegationRequest,
        session_key: AgentSessionKey,
        config: RunnableConfig,
        delegation_id: str,
        activity_writer: SubagentActivityWriter | None,
    ) -> SpecialistResult:
        """调用专业 Agent 并允许一次纯结构化修正。"""
        agent_run: SpecialistAgentRun | None = None
        try:
            agent_run = await self._build_agent(session_key)
            context = DelegationMessageContext(delegation_id=delegation_id)
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
                return await self._prepare_specialist_result(result, session_key)
            except (TypeError, ValueError, ValidationError):
                retry_output = await self._stream_specialist(
                    agent_run.agent,
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
                return await self._prepare_specialist_result(result, session_key)
        finally:
            if agent_run is not None:
                await self._cleanup_agent_run(agent_run)

    @staticmethod
    async def _cleanup_agent_run(agent_run: SpecialistAgentRun) -> None:
        """屏蔽调用方取消，确保释放 Session 锁前完成 Shell Job 清理。"""
        cleanup_task = asyncio.create_task(agent_run.shell_jobs.cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise

    @classmethod
    def _structured_response_from_message(
        cls,
        message: BaseMessage,
    ) -> SpecialistResult | None:
        """从工具调用或 Provider JSON 正文严格解析 Specialist 终态。"""
        if not isinstance(message, AIMessage):
            return None
        for call in message.tool_calls:
            if call.get("name") != _STRUCTURED_RESPONSE_TOOL_NAME:
                continue
            try:
                return SpecialistResult.model_validate(call.get("args"))
            except ValidationError:
                continue

        text = cls._text_content(message)
        if text is None:
            return None
        try:
            return SpecialistResult.model_validate_json(text)
        except ValidationError:
            return None

    @classmethod
    def _is_structured_response_message(cls, message: BaseMessage) -> bool:
        """判断消息是否属于 SpecialistResult 结构化响应。"""
        if isinstance(message, AIMessage):
            has_result_tool_call = any(
                call.get("name") == _STRUCTURED_RESPONSE_TOOL_NAME
                for call in message.tool_calls
            )
            # ProviderStrategy 将终态写入 JSON 正文；这段协议数据不能作为普通回答展示。
            return (
                has_result_tool_call
                or cls._structured_response_from_message(message) is not None
            )
        if isinstance(message, ToolMessage):
            return message.name == _STRUCTURED_RESPONSE_TOOL_NAME
        return False

    @classmethod
    def _structured_response_status(
        cls,
        message: BaseMessage,
    ) -> SubagentRunStatus | None:
        """从结构化响应消息读取本次 delegation 的终态。"""
        result = cls._structured_response_from_message(message)
        return result.status if result is not None else None

    @classmethod
    def _is_public_activity_message(cls, message: BaseMessage) -> bool:
        """筛除结构化协议消息，只保留可展示的 Agent 工作消息。"""
        return not cls._is_structured_response_message(message) and isinstance(
            message,
            AIMessage | ToolMessage,
        )

    @staticmethod
    def _text_content(message: BaseMessage) -> str | None:
        """读取模型消息中的正文文本或正文增量。"""
        content = message.content
        if isinstance(content, str):
            return content or None
        if not isinstance(content, list):
            return None
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif (
                isinstance(item, dict)
                and item.get("type") in {"text", "output_text"}
                and isinstance(item.get("text"), str)
            ):
                text_parts.append(item["text"])
        text = "".join(text_parts)
        return text or None

    @classmethod
    def _reasoning_only_message(cls, message: AIMessage) -> AIMessage | None:
        """将结构化协议响应投影为只含思考的公开消息。"""
        reasoning = reasoning_text(message)
        if reasoning is None:
            return None
        additional_kwargs: dict[str, object] = {}
        created_at = message.additional_kwargs.get(MESSAGE_CREATED_AT_KEY)
        if created_at is not None:
            additional_kwargs[MESSAGE_CREATED_AT_KEY] = created_at
        return AIMessage(
            id=message.id,
            content=[{"type": "reasoning", "reasoning": reasoning}],
            additional_kwargs=additional_kwargs,
            response_metadata=message.response_metadata,
        )

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
        """执行 Specialist 并把节点消息投影为当前 Planner 的活动流。"""
        final_values: Mapping[str, object] | None = None
        emitted_message_ids: set[str] = set()
        thinking_message_ids: set[str] = set()
        text_message_ids: set[str] = set()
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
            if (
                emit_messages
                and activity_writer is not None
                and part_type == "messages"
                and isinstance(data, tuple)
                and len(data) == 2
            ):
                message, _metadata = data
                if isinstance(message, AIMessageChunk):
                    if message.id is None:
                        continue
                    message_id = str(message.id)
                    if reasoning := reasoning_text(message):
                        reset = message_id not in thinking_message_ids
                        thinking_message_ids.add(message_id)
                        activity_writer(
                            SubagentThinkingDeltaActivity(
                                delegation_id=delegation_id,
                                analysis_id=request.analysis_id,
                                agent_type=request.agent_type,
                                session_id=request.session_id,
                                message_id=message_id,
                                delta=reasoning,
                                reset=reset,
                            )
                        )
                    if text := self._text_content(message):
                        reset = message_id not in text_message_ids
                        text_message_ids.add(message_id)
                        activity_writer(
                            SubagentMessageDeltaActivity(
                                delegation_id=delegation_id,
                                analysis_id=request.analysis_id,
                                agent_type=request.agent_type,
                                session_id=request.session_id,
                                message_id=message_id,
                                delta=text,
                                reset=reset,
                            )
                        )
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
                    public_message = message
                    if self._is_structured_response_message(message):
                        if not isinstance(message, AIMessage):
                            continue
                        reasoning_message = self._reasoning_only_message(message)
                        if reasoning_message is None:
                            continue
                        public_message = reasoning_message
                    elif not self._is_public_activity_message(message):
                        continue
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

    async def get_delegation_activity(
        self,
        analysis_id: str,
        agent_type: str,
        session_id: str,
        delegation_id: str,
    ) -> DelegationActivityHistory | None:
        """通过 Specialist Agent 状态读取一次 delegation 的消息和状态。"""
        context = DelegationMessageContext(delegation_id=delegation_id)
        session_key = self._build_session_key(analysis_id, agent_type, session_id)
        state_values = await self._read_session_state(session_key)
        messages = state_values.get("messages")
        if not isinstance(messages, list):
            return None

        found = False
        has_later_delegation = False
        delegation_status: SubagentRunStatus | None = None
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
                    has_later_delegation = True
                    break
                found = message_context.delegation_id == context.delegation_id
                continue
            if not found:
                continue
            if message.additional_kwargs.get(_INTERNAL_RETRY_KEY) is True:
                continue
            if self._is_structured_response_message(message):
                delegation_status = (
                    self._structured_response_status(message) or delegation_status
                )
                if isinstance(message, AIMessage):
                    reasoning_message = self._reasoning_only_message(message)
                    if reasoning_message is not None:
                        result.append(reasoning_message)
                continue
            if self._is_public_activity_message(message):
                result.append(message)
        if not found:
            return None

        with self._runtime_state_lock:
            is_active = session_key.checkpoint_ns in self._active_sessions
        structured_response = state_values.get("structured_response")
        try:
            latest_result = self._parse_specialist_result(structured_response)
        except (TypeError, ValueError, ValidationError):
            latest_result = None
        if not has_later_delegation and is_active:
            status: SubagentRunStatus = "running"
        elif delegation_status is not None:
            status = delegation_status
        elif not has_later_delegation and latest_result is not None:
            status = latest_result.status
        else:
            status = "cancelled"
        return DelegationActivityHistory(messages=result, status=status)

    async def _read_session_state(
        self,
        session_key: AgentSessionKey,
    ) -> Mapping[str, object]:
        """读取 Specialist Agent 合并增量通道后的完整状态。"""
        agent_run = await self._build_agent(session_key)
        try:
            checkpointer = agent_run.agent.checkpointer
            if not checkpointer:
                raise RuntimeError("Specialist Agent 未配置 Checkpointer")
            state_config = self.build_subagent_config(RunnableConfig(), session_key)
            configurable = state_config.get("configurable")
            if configurable is None:
                raise RuntimeError("Specialist Agent 状态配置缺少 configurable")
            configurable[CONFIG_KEY_CHECKPOINTER] = checkpointer
            state = await agent_run.agent.aget_state(state_config)
        finally:
            await self._cleanup_agent_run(agent_run)
        return cast(Mapping[str, object], state.values)

    async def _get_replayed_delegation_result(
        self,
        request: DelegationRequest,
        session_key: AgentSessionKey,
        delegation_id: str,
    ) -> DelegationResult | None:
        """恢复 Planner 待执行工具时复用同一 delegation 的既有结果。"""
        state_values = await self._read_session_state(session_key)
        structured_response = state_values.get("structured_response")
        messages = state_values.get("messages")
        if not isinstance(messages, list):
            return None
        try:
            result = self._parse_specialist_result(structured_response)
        except (TypeError, ValueError, ValidationError):
            return None

        found = False
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
                    continue
                if found:
                    return None
                found = message_context.delegation_id == delegation_id
        if not found:
            return None
        result = await self._prepare_specialist_result(result, session_key)
        return self._to_delegation_result(request, result)

    @staticmethod
    def _to_delegation_result(
        request: DelegationRequest,
        result: SpecialistResult,
    ) -> DelegationResult:
        """补充 Session 身份并生成委派结果。"""
        return DelegationResult(
            status=result.status,
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            content=result.content,
            artifacts=result.artifacts,
            repair_requests=result.repair_requests,
            failure_reasons=result.failure_reasons,
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
        self._session_locks.clear()
        with self._runtime_state_lock:
            self._active_sessions.clear()
            self._eval_delegations.clear()
