"""Specialist Checkpoint 的纯状态投影。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel, ValidationError

from app.assistant.agents.contracts import (
    DELEGATION_CONTEXT_KEY,
    MESSAGE_CREATED_AT_KEY,
    DelegationActivityHistory,
    DelegationCheckpointRecord,
    DelegationMessageContext,
    DelegationRequest,
    DelegationResult,
    SessionSummary,
    SpecialistResult,
    SubagentRunStatus,
)
from app.assistant.message_content import message_text, reasoning_text
from app.shared.contracts.analysis import AgentSessionKey

_INTERNAL_RETRY_KEY = "dataagent_internal_retry"
_STRUCTURED_RESPONSE_TOOL_NAME = "SpecialistResult"


def parse_specialist_result(output: object) -> SpecialistResult:
    """从 LangGraph 输出提取并严格校验结构化结果。"""
    candidate: object = output
    if isinstance(output, Mapping) and "structured_response" in output:
        candidate = output["structured_response"]
    if isinstance(candidate, SpecialistResult):
        return candidate
    if isinstance(candidate, BaseModel):
        candidate = candidate.model_dump(mode="python")
    return SpecialistResult.model_validate(candidate)


def structured_response_from_message(
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
    text = message_text(message)
    if text is None:
        return None
    try:
        return SpecialistResult.model_validate_json(text)
    except ValidationError:
        return None


def is_structured_response_message(message: BaseMessage) -> bool:
    """判断消息是否属于 SpecialistResult 结构化响应。"""
    if isinstance(message, AIMessage):
        return (
            any(
                call.get("name") == _STRUCTURED_RESPONSE_TOOL_NAME
                for call in message.tool_calls
            )
            or structured_response_from_message(message) is not None
        )
    return isinstance(message, ToolMessage) and (
        message.name == _STRUCTURED_RESPONSE_TOOL_NAME
    )


def is_public_activity_message(message: BaseMessage) -> bool:
    """筛除结构化协议消息，只保留可展示的 Agent 工作消息。"""
    return not is_structured_response_message(message) and isinstance(
        message,
        AIMessage | ToolMessage,
    )


def reasoning_only_message(message: AIMessage) -> AIMessage | None:
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

    def latest_result(self) -> SpecialistResult | None:
        """读取 Session 最近一次结构化结果。"""
        try:
            return parse_specialist_result(self._values.get("structured_response"))
        except (TypeError, ValueError, ValidationError):
            return None

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
                if found:
                    break
                found = context.delegation_id == delegation_id
                continue
            if not found or message.additional_kwargs.get(_INTERNAL_RETRY_KEY) is True:
                continue
            if is_structured_response_message(message):
                if isinstance(message, AIMessage):
                    reasoning_message = reasoning_only_message(message)
                    if reasoning_message is not None:
                        result.append(reasoning_message)
                continue
            if is_public_activity_message(message):
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
        return DelegationResult(
            status=result.status,
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            content=result.content,
            artifacts=result.artifacts,
            warnings=result.warnings,
            repair_requests=result.repair_requests,
            failure_reasons=result.failure_reasons,
        )

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
            artifact_count=len(result.artifacts) if result else 0,
            updated_at=updated_at,
        )
