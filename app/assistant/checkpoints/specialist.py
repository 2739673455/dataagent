"""Specialist Checkpoint 的纯状态投影。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import ValidationError

from app.assistant.events.content import is_final_assistant_message, message_text
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
    """把 Specialist 物化状态投影为稳定的查询模型。"""

    def __init__(self, values: Mapping[str, object]) -> None:
        """绑定一次读取到的 Checkpoint channel values。"""
        self._values = values

    @property
    def messages(self) -> list[BaseMessage]:
        """返回 Checkpoint 中有效的 LangChain 消息。"""
        messages = self._values.get("messages")
        if not isinstance(messages, list):
            return []
        return [message for message in messages if isinstance(message, BaseMessage)]

    def delegation_record(
        self,
        delegation_id: str,
    ) -> DelegationCheckpointRecord | None:
        """读取一次委派的显式持久化状态。"""
        records = self._values.get("delegation_records")
        if not isinstance(records, Mapping):
            return None
        raw_record = records.get(delegation_id)
        try:
            return DelegationCheckpointRecord.model_validate(raw_record)
        except ValidationError:
            return None

    def latest_result(self) -> DelegationResult | None:
        """读取 Session 最近一次由运行时记录的结果。"""
        for message in reversed(self.messages):
            raw_context = message.additional_kwargs.get(DELEGATION_CONTEXT_KEY)
            if raw_context is None:
                continue
            try:
                context = DelegationMessageContext.model_validate(raw_context)
            except ValidationError:
                continue
            record = self.delegation_record(context.delegation_id)
            if record is not None:
                return record.result
            break
        return None

    def final_response(self, delegation_id: str) -> str | None:
        """仅取当前委派最后一条完整终答，不复用旧轮次或工具调用前的文本。"""
        found = False
        response: str | None = None
        for message in self.messages:
            raw_context = message.additional_kwargs.get(DELEGATION_CONTEXT_KEY)
            if raw_context is not None:
                try:
                    context = DelegationMessageContext.model_validate(raw_context)
                except ValidationError:
                    if found:
                        break
                    continue
                if found and context.delegation_id != delegation_id:
                    break
                found = context.delegation_id == delegation_id
                response = None
                continue
            if found:
                response = (
                    message_text(message)
                    if is_final_assistant_message(message)
                    else None
                )
        return response

    def delegation_activity(
        self,
        delegation_id: str,
        *,
        active: bool,
    ) -> DelegationActivityHistory | None:
        """按显式边界和状态投影一次 delegation 的公开消息。"""
        found = False
        result: list[BaseMessage] = []
        for message in self.messages:
            raw_context = message.additional_kwargs.get(DELEGATION_CONTEXT_KEY)
            if raw_context is not None:
                try:
                    context = DelegationMessageContext.model_validate(raw_context)
                except ValidationError:
                    if found:
                        break
                    continue
                if found and context.delegation_id != delegation_id:
                    break
                if context.delegation_id == delegation_id:
                    found = True
                continue
            if found and isinstance(message, AIMessage | ToolMessage):
                result.append(message)
        if not found:
            return None

        record = self.delegation_record(delegation_id)
        # Checkpoint 可能停在进程退出前写入的 running 状态；没有对应活跃任务时，
        # 对外必须按已中断处理，不能让历史页面永久显示运行中。
        status: SubagentRunStatus = (
            "running"
            if active
            else "cancelled"
            if record is None or record.status == "running"
            else record.status
        )
        return DelegationActivityHistory(messages=result, status=status)

    def replayed_result(
        self,
        request: DelegationRequest,
        delegation_id: str,
    ) -> DelegationResult | None:
        """从显式委派记录恢复 Planner 待执行工具的既有结果。"""
        record = self.delegation_record(delegation_id)
        if record is None or record.result is None:
            return None
        result = record.result
        if (result.analysis_id, result.agent_type, result.session_id) != (
            request.analysis_id,
            request.agent_type,
            request.session_id,
        ):
            raise ValueError("委派记录与请求的 Session 身份不一致")
        return result

    def session_summary(
        self,
        session_key: AgentSessionKey,
        *,
        active: bool,
        updated_at: datetime | None,
    ) -> SessionSummary:
        """投影 Session 列表中的摘要状态。"""
        result = self.latest_result()
        return SessionSummary(
            analysis_id=session_key.analysis_id,
            agent_type=session_key.agent_type,
            session_id=session_key.session_id,
            status=("active" if active else result.status if result else "interrupted"),
            summary=result.content if result else None,
            updated_at=updated_at,
        )
