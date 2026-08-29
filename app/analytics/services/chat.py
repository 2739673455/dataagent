import asyncio
import base64
import json
import mimetypes
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
from loguru import logger
from pydantic import ValidationError

from app.analytics.agents.contracts import (
    MESSAGE_CREATED_AT_KEY,
    ConversationAgentRuntime,
    DelegationResult,
    PlannerTurnContext,
    build_planner_config,
)
from app.analytics.agents.user_message_metadata import (
    USER_MESSAGE_METADATA_KEY,
    UserMessageMetadata,
)
from app.analytics.api.chat import schemas as chat_schema
from app.analytics.services.contracts import (
    AgentRuntimeManager,
    ConversationFileReader,
)

_IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
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
                args=tool_call.get("args", {}),
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


async def _build_image_data_url(
    sandbox: ConversationFileReader,
    user_id: int,
    conversation_id: UUID,
    attachment: chat_schema.AttachmentReference,
) -> str:
    """读取沙箱中的图片附件，并转换为 data URL"""
    mime_type, _ = mimetypes.guess_type(attachment.f_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    content = await sandbox.download_file(
        user_id,
        conversation_id,
        attachment.f_path,
    )
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _append_prompt(
    content_parts: list[dict[str, Any]],
    header: str,
    lines: list[str],
) -> None:
    """向 content_parts 追加提示文本，与已有内容间用换行符分隔"""
    prefix = "\n\n" if content_parts else ""
    content_parts.append(
        chat_schema.TextContent(
            type="text", text=prefix + header + "\n" + "\n".join(lines)
        ).model_dump()
    )


async def _process_attachments(
    sandbox: ConversationFileReader,
    content_parts: list[dict[str, Any]],
    attachments: list[chat_schema.AttachmentReference],
    user_id: int,
    conversation_id: UUID,
) -> None:
    """处理附件：文件追加提示文本，图片转换为 base64 data URL"""
    images: list[chat_schema.AttachmentReference] = []
    documents: list[chat_schema.AttachmentReference] = []

    for attachment in attachments:
        suffix = (
            attachment.f_path.rsplit(".", 1)[-1].lower()
            if "." in attachment.f_path
            else ""
        )
        (images if suffix in _IMAGE_SUFFIXES else documents).append(attachment)

    if documents:
        _append_prompt(
            content_parts,
            "用户上传的以下文件已保存到当前工作区，可直接读取：",
            [f"- 文件：`{attachment.f_path}`" for attachment in documents],
        )

    if not images:
        return

    lost: list[str] = []
    for attachment in images:
        try:
            content_parts.append(
                chat_schema.ImageContent(
                    type="image_url",
                    image_url=await _build_image_data_url(
                        sandbox,
                        user_id,
                        conversation_id,
                        attachment,
                    ),
                ).model_dump()
            )
        except OSError:
            logger.warning(
                "附件图片不可用: "
                f"conversation_id={conversation_id}, file={attachment.f_path}"
            )
            lost.append(f"- 图片：`{attachment.f_path}`")
    if lost:
        _append_prompt(
            content_parts,
            "用户之前上传了一些图片，但图片当前已不存在：",
            lost,
        )


async def _schema_to_human_message(
    sandbox: ConversationFileReader,
    message: chat_schema.UserMessageRequest,
    user_id: int,
    conversation_id: UUID,
) -> HumanMessage:
    """将用户消息转换为 LangChain 消息"""
    content_parts = [part.model_dump() for part in message.parts]

    if message.attachments:
        await _process_attachments(
            sandbox,
            content_parts,
            message.attachments,
            user_id,
            conversation_id,
        )

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
    return HumanMessage(
        id=str(uuid.uuid4()),
        content=cast(list[str | dict[Any, Any]], content_parts),
        additional_kwargs={
            MESSAGE_PAYLOAD_KEY: metadata,
            USER_MESSAGE_METADATA_KEY: UserMessageMetadata(
                received_at=received_at
            ).model_dump(mode="json"),
        },
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


async def _execute_agent(
    input_messages: list[BaseMessage],
    runtime: ConversationAgentRuntime,
    turn_context: PlannerTurnContext,
) -> AsyncGenerator[dict[str, Any]]:
    """执行 Agent 并流式返回原始更新"""
    config = build_planner_config(
        turn_context.user_id,
        turn_context.conversation_id,
    )
    config.setdefault("configurable", {})["planner_run_id"] = (
        turn_context.planner_run_id
    )
    async for chunk in runtime.planner.astream(
        input={"messages": input_messages},
        config=config,
    ):
        yield chunk


async def run_agent_turn(
    agents: AgentRuntimeManager,
    sandbox: ConversationFileReader,
    user_id: int,
    conversation_id: UUID,
    user_message: chat_schema.UserMessageRequest,
    cancel: asyncio.Event,
) -> AsyncGenerator[chat_schema.MessageResponse]:
    """执行一轮 Agent 对话并流式返回响应"""
    logger.info(
        f"智能体回合开始: conversation_id={conversation_id}, "
        f"parts={len(user_message.parts)}, "
        f"attachments={len(user_message.attachments or ())}"
    )

    input_messages: list[BaseMessage] = [
        await _schema_to_human_message(
            sandbox,
            user_message,
            user_id,
            conversation_id,
        )
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

                responses: list[chat_schema.MessageResponse] = []
                for node in ("model", "tools"):
                    messages = chunk.get(node, {}).get("messages")
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
                    yield response

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
