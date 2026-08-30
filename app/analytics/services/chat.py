import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.types import StreamPart
from loguru import logger
from pydantic import ValidationError

from app.analytics.agents.contracts import (
    MESSAGE_CREATED_AT_KEY,
    ConversationAgentRuntime,
    DelegationResult,
    PlannerTurnContext,
    SubagentActivity,
    SubagentMessageActivity,
    SubagentStatusActivity,
    build_planner_config,
)
from app.analytics.agents.explorer.semantic_recall_middleware import (
    expand_semantic_recall_messages_for_display,
)
from app.analytics.agents.middleware.user_message_attachments import (
    USER_MESSAGE_ATTACHMENTS_KEY,
    UserMessageAttachment,
    UserMessageAttachments,
)
from app.analytics.agents.middleware.user_message_metadata import (
    USER_MESSAGE_METADATA_KEY,
    UserMessageMetadata,
)
from app.analytics.api.chat import schemas as chat_schema
from app.analytics.services.contracts import AgentRuntimeManager

MESSAGE_PAYLOAD_KEY = "dataagent_message"


def _message_created_at(message: BaseMessage) -> datetime | None:
    """读取消息创建时间"""
    value = message.additional_kwargs.get(MESSAGE_CREATED_AT_KEY)
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _content_to_parts(content: Any) -> list[chat_schema.MessagePart]:
    """将 LangChain 消息内容转换为接口消息片段"""
    if isinstance(content, str):
        return [chat_schema.TextContent(type="text", text=content)]
    if not isinstance(content, list):
        return [chat_schema.TextContent(type="text", text=str(content))]

    parts: list[chat_schema.MessagePart] = []
    for item in content:
        if isinstance(item, str):
            parts.append(chat_schema.TextContent(type="text", text=item))
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"text", "input_text", "output_text"} and isinstance(
            item.get("text"), str
        ):
            parts.append(chat_schema.TextContent(type="text", text=item["text"]))
            continue
        if item.get("type") != "image_url":
            continue
        image_url = item.get("image_url")
        if isinstance(image_url, dict):
            image_url = image_url.get("url")
        if isinstance(image_url, str):
            parts.append(
                chat_schema.ImageContent(type="image_url", image_url=image_url)
            )
    return parts


def _schema_from_metadata(
    message: BaseMessage,
) -> chat_schema.MessageResponse | None:
    """从 LangGraph 消息元数据恢复用户原始消息"""
    payload = message.additional_kwargs.get(MESSAGE_PAYLOAD_KEY)
    if not isinstance(payload, dict):
        return None
    try:
        schema = chat_schema.MessageResponse.model_validate(payload)
    except ValidationError:
        logger.warning(f"持久化消息元数据无效: message_id={message.id}")
        return None
    return schema.model_copy(update={"message_id": message.id})


def _delegation_result_attachments(
    message: ToolMessage,
) -> list[chat_schema.Attachment]:
    """从委派结果的稳定协议中提取可下载产物"""
    if message.name != "delegation":
        return []
    content = message.content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return []
    elif isinstance(content, dict):
        payload = content
    else:
        return []
    try:
        result = DelegationResult.model_validate(payload)
    except ValidationError:
        logger.warning(
            f"委派结果载荷无效: message_id={message.id}, "
            f"tool_call_id={message.tool_call_id}"
        )
        return []
    if result.model_dump(mode="json") != payload:
        logger.warning(
            f"委派结果载荷不是规范化形式: message_id={message.id}, "
            f"tool_call_id={message.tool_call_id}"
        )
        return []
    return [
        chat_schema.Attachment(
            f_path=artifact.path.removeprefix("/"),
            media_type=artifact.media_type,
            description=artifact.description,
        )
        for artifact in result.artifacts
    ]


def _langchain_message_to_schema(
    message: BaseMessage,
) -> chat_schema.MessageResponse | None:
    """将 LangChain 消息转换为接口消息"""
    if stored_schema := _schema_from_metadata(message):
        return stored_schema
    if isinstance(message, ToolMessage):
        return chat_schema.MessageResponse(
            message_id=message.id,
            created_at=_message_created_at(message),
            role="tool",
            parts=[
                chat_schema.ToolResultPart(
                    type="tool_result",
                    tool_call_id=message.tool_call_id,
                    name=message.name or "",
                    content=str(message.content),
                )
            ],
            attachments=_delegation_result_attachments(message) or None,
        )

    if isinstance(message, AIMessage):
        role: chat_schema.MessageRole = "assistant"
    elif isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, ChatMessage) and message.role in {
        "user",
        "assistant",
        "tool",
        "system",
    }:
        role = cast(chat_schema.MessageRole, message.role)
    else:
        return None

    parts = _content_to_parts(message.content)
    if isinstance(message, AIMessage):
        parts.extend(
            chat_schema.ToolCallPart(
                type="tool_call",
                tool_call_id=tool_call.get("id") or "",
                name=tool_call.get("name") or "",
                args=cast(dict[str, object], tool_call.get("args", {})),
            )
            for tool_call in message.tool_calls
        )

    return chat_schema.MessageResponse(
        message_id=message.id,
        created_at=_message_created_at(message),
        role=role,
        parts=parts,
        finish_reason=cast(
            chat_schema.FinishReason | None,
            message.response_metadata.get("finish_reason"),
        ),
    )


async def _subagent_activity_to_event(
    activity: SubagentActivity,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.ChatStreamEventPayload | None:
    """把受信任的 Agent 内部活动投影为公开聊天事件"""
    if isinstance(activity, SubagentMessageActivity):
        expanded = await expand_semantic_recall_messages_for_display(
            [activity.message],
            user_id,
            conversation_id,
        )
        message = _langchain_message_to_schema(expanded[0])
        if message is None:
            return None
        return chat_schema.ChatStreamSubagentMessageEvent(
            type="subagent_message",
            delegation_id=activity.delegation_id,
            analysis_id=activity.analysis_id,
            agent_type=activity.agent_type,
            session_id=activity.session_id,
            message=message,
        )
    if isinstance(activity, SubagentStatusActivity):
        return chat_schema.ChatStreamSubagentStatusEvent(
            type="subagent_status",
            delegation_id=activity.delegation_id,
            analysis_id=activity.analysis_id,
            agent_type=activity.agent_type,
            session_id=activity.session_id,
            status=activity.status,
        )
    return None


def _schema_to_human_message(
    message: chat_schema.UserMessageRequest,
) -> HumanMessage:
    """将用户消息转换为 LangChain 消息"""
    content_parts = [part.model_dump() for part in message.parts]

    stored_parts: list[chat_schema.MessagePart] = [*message.parts]
    received_at = datetime.now(UTC)
    metadata = chat_schema.MessageResponse(
        created_at=received_at,
        role="user",
        parts=stored_parts,
        attachments=(
            [
                chat_schema.Attachment(f_path=attachment.f_path)
                for attachment in message.attachments
            ]
            if message.attachments
            else None
        ),
    ).model_dump(mode="json", exclude={"message_id"})
    additional_kwargs: dict[str, Any] = {
        MESSAGE_PAYLOAD_KEY: metadata,
        USER_MESSAGE_METADATA_KEY: UserMessageMetadata(
            received_at=received_at
        ).model_dump(mode="json"),
    }
    if message.attachments:
        additional_kwargs[USER_MESSAGE_ATTACHMENTS_KEY] = UserMessageAttachments(
            attachments=[
                UserMessageAttachment(f_path=attachment.f_path)
                for attachment in message.attachments
            ]
        ).model_dump(mode="json")
    return HumanMessage(
        id=str(uuid.uuid4()),
        content=cast(list[str | dict[Any, Any]], content_parts),
        additional_kwargs=additional_kwargs,
    )


async def list_messages(
    agents: AgentRuntimeManager,
    user_id: int,
    conversation_id: UUID,
) -> list[chat_schema.MessageResponse]:
    """从 LangGraph 最新线程状态读取消息"""
    runtime = await agents.get_conversation_runtime(user_id, conversation_id)
    state = await runtime.planner.aget_state(
        build_planner_config(user_id, conversation_id)
    )
    messages = state.values.get("messages", [])
    if not isinstance(messages, list):
        return []

    result: list[chat_schema.MessageResponse] = []
    for message in messages:
        if not isinstance(message, BaseMessage):
            continue
        if schema := _langchain_message_to_schema(message):
            result.append(schema)
    return result


async def list_subagent_messages(
    agents: AgentRuntimeManager,
    user_id: int,
    conversation_id: UUID,
    analysis_id: str,
    agent_type: str,
    session_id: str,
    delegation_id: str,
) -> list[chat_schema.MessageResponse] | None:
    """读取一次 Specialist delegation 的公开工作消息"""
    runtime = await agents.get_conversation_runtime(user_id, conversation_id)
    messages = await runtime.session_service.get_delegation_messages(
        analysis_id,
        agent_type,
        session_id,
        delegation_id,
    )
    if messages is None:
        return None
    messages = await expand_semantic_recall_messages_for_display(
        messages,
        user_id,
        conversation_id,
    )
    return [
        schema
        for message in messages
        if (schema := _langchain_message_to_schema(message)) is not None
    ]


async def _execute_agent(
    input_messages: list[BaseMessage],
    runtime: ConversationAgentRuntime,
    turn_context: PlannerTurnContext,
) -> AsyncGenerator[StreamPart[Any, Any]]:
    """执行 Agent 并流式返回原始更新"""
    config = build_planner_config(
        turn_context.user_id,
        turn_context.conversation_id,
    )
    async for chunk in runtime.planner.astream(
        input={"messages": input_messages},
        config=config,
        stream_mode=["updates", "custom"],
        version="v2",
    ):
        yield chunk


async def run_agent_turn(
    agents: AgentRuntimeManager,
    user_id: int,
    conversation_id: UUID,
    user_message: chat_schema.UserMessageRequest,
    cancel: asyncio.Event,
) -> AsyncGenerator[chat_schema.ChatStreamEventPayload]:
    """执行一轮 Agent 对话并流式返回响应"""
    logger.info(
        f"智能体回合开始: conversation_id={conversation_id}, "
        f"parts={len(user_message.parts)}, "
        f"attachments={len(user_message.attachments or ())}"
    )

    input_messages: list[BaseMessage] = [
        _schema_to_human_message(user_message)
    ]

    runtime = await agents.get_conversation_runtime(user_id, conversation_id)
    async with agents.execution(
        user_id,
        conversation_id,
        runtime=runtime,
    ) as turn_context:
        continuation_count = 0
        while True:
            last_finish_reason: str | None = None

            async for chunk in _execute_agent(
                input_messages,
                runtime,
                turn_context,
            ):
                if cancel.is_set():
                    logger.info(f"智能体执行已取消: conversation_id={conversation_id}")
                    break

                if chunk.get("type") == "custom":
                    activity = chunk.get("data")
                    if isinstance(
                        activity,
                        (SubagentMessageActivity, SubagentStatusActivity),
                    ):
                        event = await _subagent_activity_to_event(
                            activity,
                            user_id,
                            conversation_id,
                        )
                        if event is not None:
                            yield event
                    continue
                if chunk.get("type") != "updates":
                    continue
                data = chunk.get("data")
                if not isinstance(data, dict):
                    continue

                responses: list[chat_schema.MessageResponse] = []
                for node in ("model", "tools"):
                    update = data.get(node)
                    messages = (
                        update.get("messages") if isinstance(update, dict) else None
                    )
                    if not isinstance(messages, list):
                        continue
                    for message in messages:
                        if response := _langchain_message_to_schema(message):
                            responses.append(response)
                logger.debug(
                    f"智能体流式更新: conversation_id={conversation_id}, "
                    f"nodes={tuple(chunk)}, "
                    f"messages={len(responses)}"
                )
                for response in responses:
                    last_finish_reason = response.finish_reason
                    yield chat_schema.ChatStreamMessageEvent(
                        type="message",
                        message=response,
                    )

            if (
                cancel.is_set()
                or last_finish_reason is None
                or last_finish_reason == "stop"
            ):
                break
            if continuation_count >= turn_context.max_continuations:
                raise PlannerContinuationLimitError(
                    turn_context.max_continuations,
                    last_finish_reason,
                )

            continuation_count += 1
            # 空增量会保留 Checkpointer 中的已有状态并继续生成
            input_messages = []

    logger.info(f"智能体回合结束: conversation_id={conversation_id}")


class PlannerContinuationLimitError(RuntimeError):
    """Planner 自动续写次数超过服务端硬限制"""

    def __init__(self, max_continuations: int, finish_reason: str) -> None:
        """初始化包含续写上限和结束原因的异常"""
        self.max_continuations = max_continuations
        self.finish_reason = finish_reason
        super().__init__(
            f"规划器在结束原因 {finish_reason!r} 下连续续写次数超过上限 ({max_continuations} 次)"
        )
