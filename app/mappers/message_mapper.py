import base64
import json
import mimetypes
import uuid
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

from app.clients.docker_sandbox_manager import docker_sandbox_manager
from app.routes.api.v1.chat import schemas as chat_schema

_MESSAGE_METADATA_KEY = "dataagent_message"


def _content_to_parts(content: Any) -> list[chat_schema.MessagePart]:
    """将 LangChain 消息内容转换为接口消息片段"""
    if isinstance(content, str):
        return [chat_schema.TextContent(text=content)]
    if not isinstance(content, list):
        return [chat_schema.TextContent(text=str(content))]

    parts: list[chat_schema.MessagePart] = []
    for item in content:
        if isinstance(item, str):
            parts.append(chat_schema.TextContent(text=item))
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"text", "input_text", "output_text"} and isinstance(
            item.get("text"), str
        ):
            parts.append(chat_schema.TextContent(text=item["text"]))
            continue
        if item.get("type") != "image_url":
            continue
        image_url = item.get("image_url")
        if isinstance(image_url, dict):
            image_url = image_url.get("url")
        if isinstance(image_url, str):
            parts.append(chat_schema.ImageContent(image_url=image_url))
    return parts


def _schema_from_metadata(
    message: BaseMessage,
) -> chat_schema.MessageSchema | None:
    """从 LangGraph 消息元数据恢复用户原始消息"""
    payload = message.additional_kwargs.get(_MESSAGE_METADATA_KEY)
    if not isinstance(payload, dict):
        return None
    try:
        schema = chat_schema.MessageSchema.model_validate(payload)
    except ValidationError:
        logger.warning(f"Invalid persisted message metadata: message_id={message.id}")
        return None
    return schema.model_copy(update={"message_id": message.id})


def _tool_message_to_schema(message: ToolMessage) -> chat_schema.MessageSchema:
    """将工具结果转换为接口消息"""
    attachments: list[chat_schema.Attachment] | None = None
    if message.name == "return_file" and isinstance(message.content, str):
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("status") == "success":
            f_path = payload.get("f_path")
            if isinstance(f_path, str):
                attachments = [chat_schema.Attachment(f_path=f_path)]

    return chat_schema.MessageSchema(
        message_id=message.id,
        role="tool",
        parts=[
            chat_schema.ToolResultPart(
                tool_call_id=message.tool_call_id,
                name=message.name or "",
                content=str(message.content),
            )
        ],
        attachments=attachments,
    )


def langchain_message_to_schema(
    message: BaseMessage,
) -> chat_schema.MessageSchema | None:
    """将 LangChain 消息转换为接口消息"""
    if stored_schema := _schema_from_metadata(message):
        return stored_schema
    if isinstance(message, ToolMessage):
        return _tool_message_to_schema(message)

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
                tool_call_id=tool_call.get("id") or "",
                name=tool_call.get("name") or "",
                args=tool_call.get("args", {}),
            )
            for tool_call in message.tool_calls
        )

    return chat_schema.MessageSchema(
        message_id=message.id,
        role=role,
        parts=parts,
        finish_reason=cast(
            chat_schema.FinishReason | None,
            message.response_metadata.get("finish_reason"),
        ),
    )


def agent_chunk_to_schemas(chunk: dict) -> list[chat_schema.MessageSchema]:
    """将 Agent 流式输出块中的模型消息和工具消息转换为 MessageSchema 列表"""
    schemas: list[chat_schema.MessageSchema] = []
    # 处理 model 和 tools 两类节点的返回消息
    # {'model': {'messages': [AIMessage, ChatMessage]}}
    # {'tools': {'messages': [ToolMessage]}}
    for key in ("model", "tools"):
        messages = chunk.get(key, {}).get("messages")
        if not isinstance(messages, list):
            continue
        for m in messages:
            if s := langchain_message_to_schema(m):
                schemas.append(s)
    return schemas


async def _build_image_data_url(
    user_id: int, conversation_id: UUID, attachment: chat_schema.Attachment
) -> str:
    """读取沙盒中的图片附件，并转换为 data URL"""
    # 根据文件名推断 MIME 类型，供 data URL 正确声明图片格式
    mime_type, _ = mimetypes.guess_type(attachment.f_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    # 将图片二进制编码为 base64，并拼接成模型可直接消费的 data URL
    content = await docker_sandbox_manager.download_file(
        user_id,
        conversation_id,
        attachment.f_path,
    )
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _append_prompt(
    content_parts: list[dict[str, Any]], header: str, lines: list[str]
) -> None:
    """向 content_parts 追加提示文本，与已有内容间用换行符分隔"""
    prefix = "\n\n" if content_parts else ""
    content_parts.append(
        chat_schema.TextContent(
            text=prefix + header + "\n" + "\n".join(lines)
        ).model_dump()
    )


_IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


async def _process_attachments(
    content_parts: list[dict[str, Any]],
    attachments: list[chat_schema.Attachment],
    user_id: int | None,
    conversation_id: UUID | None,
) -> None:
    """处理附件：文件追加提示文本，图片转换为 base64 data URL"""
    images: list[chat_schema.Attachment] = []
    docs: list[chat_schema.Attachment] = []

    for a in attachments:
        # 获取文件后缀
        suffix = a.f_path.rsplit(".", 1)[-1].lower() if "." in a.f_path else ""
        # 根据文件类型添加到相应列表
        (images if suffix in _IMAGE_SUFFIXES else docs).append(a)

    # 文档：在 prompt 中添加文本提示，告知模型文件已保存到工作区
    if docs:
        _append_prompt(
            content_parts,
            "用户上传的以下文件已保存到当前工作区，可直接读取：",
            [f"- 文件：`{a.f_path}`" for a in docs],
        )

    # 图片：从工作区读取并转换为 base64 data URL，无法加载的图片记录到 lost 列表
    if images:
        # 需要从工作区读取图片，获取工作区目录依赖 user_id 和 conversation_id
        # 如果缺少 user_id 或 conversation_id，则报错
        if user_id is None or conversation_id is None:
            raise ValueError(
                "user_id and conversation_id are required for image attachments"
            )
        lost: list[str] = []
        for a in images:
            try:
                # 将图片转换为 base64 内容，添加到 content_parts
                content_parts.append(
                    chat_schema.ImageContent(
                        image_url=await _build_image_data_url(
                            user_id,
                            conversation_id,
                            a,
                        )
                    ).model_dump()
                )
            except OSError:
                logger.warning(
                    f"Attachment image is unavailable: conversation_id={conversation_id}, file={a.f_path}"
                )
                # 记录缺失的图片
                lost.append(f"- 图片：`{a.f_path}`")
        # 图片缺失提示
        if lost:
            _append_prompt(
                content_parts,
                "用户之前上传了一些图片，但图片当前已不存在：",
                lost,
            )


async def schema_to_human_message(
    message: chat_schema.MessageSchema,
    user_id: int,
    conversation_id: UUID,
) -> HumanMessage:
    """将用户消息转换为 LangChain 消息"""
    if message.role != "user":
        raise ValueError("Only user messages can be submitted to the agent")

    content_parts: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, (chat_schema.TextContent, chat_schema.ImageContent)):
            content_parts.append(part.model_dump())
        else:
            raise TypeError("User messages only support text and image parts")

    if message.attachments:
        await _process_attachments(
            content_parts, message.attachments, user_id, conversation_id
        )

    metadata = message.model_dump(mode="json", exclude={"message_id"})
    return HumanMessage(
        id=str(uuid.uuid4()),
        content=cast(list[str | dict[Any, Any]], content_parts),
        additional_kwargs={_MESSAGE_METADATA_KEY: metadata},
    )
