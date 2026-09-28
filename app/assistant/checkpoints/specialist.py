"""读取专业 Agent 的委派消息、运行状态和文本结果。"""

from collections.abc import Mapping
from datetime import datetime

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import ValidationError

from app.assistant.events.content import message_text
from app.assistant.execution.types import (
    DELEGATION_CONTEXT_KEY,
    DelegationActivityHistory,
    DelegationCheckpointRecord,
    DelegationMessageContext,
    DelegationRequest,
    DelegationResult,
    SessionSummary,
    SubagentRunStatus,
)
from app.shared.contracts.analysis import AgentSessionKey


class SpecialistCheckpointView:
    """按委派边界读取专业 Agent 的 Checkpoint。"""

    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = values

    @property
    def messages(self) -> list[BaseMessage]:
        """读取已持久化的消息。"""
        messages = self._values.get("messages")
        return (
            [m for m in messages if isinstance(m, BaseMessage)]
            if isinstance(messages, list)
            else []
        )

    def delegation_record(
        self, delegation_id: str
    ) -> DelegationCheckpointRecord | None:
        """读取指定委派的运行记录。"""
        records = self._values.get("delegation_records")
        if not isinstance(records, Mapping):
            return None
        try:
            return DelegationCheckpointRecord.model_validate(records.get(delegation_id))
        except ValidationError:
            return None

    def _messages_for(self, delegation_id: str) -> list[BaseMessage] | None:
        """按用户消息中的委派标识截取当前委派消息。"""
        found = False
        messages: list[BaseMessage] = []
        for message in self.messages:
            raw = message.additional_kwargs.get(DELEGATION_CONTEXT_KEY)
            if raw is not None:
                try:
                    context = DelegationMessageContext.model_validate(raw)
                except ValidationError:
                    if found:
                        break
                    continue
                if found and context.delegation_id != delegation_id:
                    break
                found = context.delegation_id == delegation_id
            elif found and isinstance(message, AIMessage | ToolMessage):
                messages.append(message)
        return messages if found else None

    def plain_response(self, delegation_id: str) -> str | None:
        """读取当前委派最后一条无工具调用的模型文本。"""
        messages = self._messages_for(delegation_id) or []
        if (
            not messages
            or not isinstance(messages[-1], AIMessage)
            or messages[-1].tool_calls
        ):
            return None
        return message_text(messages[-1])

    def delegation_activity(
        self, delegation_id: str, *, active: bool
    ) -> DelegationActivityHistory | None:
        """读取委派消息和状态，没有活跃任务的 running 记录视为中断。"""
        messages = self._messages_for(delegation_id)
        if messages is None:
            return None
        record = self.delegation_record(delegation_id)
        status: SubagentRunStatus = (
            "running"
            if active
            else "cancelled"
            if record is None or record.status == "running"
            else record.status
        )
        return DelegationActivityHistory(messages=messages, status=status)

    def replayed_result(
        self, request: DelegationRequest, delegation_id: str
    ) -> DelegationResult | None:
        """复用同一委派已经保存的结果。"""
        record = self.delegation_record(delegation_id)
        if (
            record is None
            or record.result is None
            or record.status not in {"completed", "failed"}
        ):
            return None
        return DelegationResult(
            status="completed" if record.status == "completed" else "failed",
            content=record.result,
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
        )

    def session_summary(
        self, session_key: AgentSessionKey, *, active: bool, updated_at: datetime | None
    ) -> SessionSummary:
        """使用最近一次委派记录构造会话摘要。"""
        record = None
        for message in reversed(self.messages):
            raw = message.additional_kwargs.get(DELEGATION_CONTEXT_KEY)
            if raw is not None:
                try:
                    context = DelegationMessageContext.model_validate(raw)
                except ValidationError:
                    continue
                record = self.delegation_record(context.delegation_id)
                break
        return SessionSummary(
            analysis_id=session_key.analysis_id,
            agent_type=session_key.agent_type,
            session_id=session_key.session_id,
            status="active"
            if active
            else "completed"
            if record and record.status == "completed"
            else "failed"
            if record and record.status == "failed"
            else "interrupted",
            summary=record.result if record else None,
            updated_at=updated_at,
        )
