import base64
import json
import mimetypes
from datetime import datetime
from typing import Any, cast

from langchain.messages import AIMessage, ToolMessage
from langchain_core.messages import ChatMessage
from loguru import logger

from app.agent.agent import get_workspace_dir
from app.entities.chat import Message
from app.schemas import chat_schema


def entity_to_schema(message: Message) -> chat_schema.MessageSchema:
    """将消息实体转换为 MessageSchema"""
    # 将 json 字符串转换为消息片段对象
    parts: list[chat_schema.MessagePart] = []
    for item in json.loads(message.parts):
        schema = {
            "text": chat_schema.TextContent,
            "image_url": chat_schema.ImageContent,
            "tool_call": chat_schema.ToolCallPart,
            "tool_result": chat_schema.ToolResultPart,
        }.get(item["type"])
        if schema is None:
            raise ValueError(f"Unsupported message part type: {item['type']}")
        parts.append(schema(**item))

    # 将 json 字符串转换为附件对象
    attachments = None
    if message.attachments:
        attachments = [chat_schema.Attachment(**item) for item in json.loads(message.attachments)]

    return chat_schema.MessageSchema(
        message_id=message.id,
        context_seq=message.context_seq,
        role=cast(chat_schema.MessageRole, message.role),
        parts=parts,
        attachments=attachments,
        finish_reason=cast(chat_schema.FinishReason | None, message.finish_reason),
        timestamp=message.create_at,
    )


def schema_to_entity(message: chat_schema.MessageSchema, conversation_id: int) -> Message:
    """将 MessageSchema 转换为消息实体"""
    # 检查是否有上下文顺序号
    if message.context_seq is None:
        raise ValueError("Message context_seq is required")

    # 将消息片段对象转换为 json 字符串
    parts = json.dumps([part.model_dump() for part in message.parts], ensure_ascii=False)

    # 将附件对象转换为 json 字符串
    attachments = None
    if message.attachments:
        attachments = json.dumps(
            [attachment.model_dump() for attachment in message.attachments],
            ensure_ascii=False,
        )

    entity = Message(
        conversation_id=conversation_id,
        context_seq=message.context_seq,
        role=message.role,
        parts=parts,
        attachments=attachments,
        finish_reason=message.finish_reason,
    )

    if message.message_id is not None:
        entity.id = message.message_id
    if message.timestamp is not None:
        entity.create_at = message.timestamp

    return entity


def langchain_message_to_schema(
    message: AIMessage | ChatMessage | ToolMessage,
) -> chat_schema.MessageSchema | None:
    """
    将 LangChain 消息转换为 MessageSchema，同时添加时间戳。

    主要处理模型输出消息，包括 AIMessage、ChatMessage 和 ToolMessage。
    """
    timestamp = datetime.now()

    # 处理 AIMessage / ChatMessage
    if isinstance(message, (AIMessage, ChatMessage)):
        # 获取消息内容
        content = message.content
        # 如果消息内容是字符串，转换为文本消息片段
        if isinstance(content, str):
            parts: list[chat_schema.MessagePart] = [chat_schema.TextContent(text=content)]
        # 如果消息内容是列表，转换为文本消息片段列表
        elif isinstance(content, list):
            parts = [
                chat_schema.TextContent(text=item["text"])
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
        # 其他情况，转换为文本消息片段
        else:
            parts = [chat_schema.TextContent(text=str(content))]

        # 追加 tool_calls（仅 AIMessage 具有该属性）
        if isinstance(message, AIMessage) and message.tool_calls:
            parts.extend(
                chat_schema.ToolCallPart(
                    tool_call_id=tool_call.get("id") or "",
                    name=tool_call.get("name") or "",
                    args=tool_call.get("args", {}),
                )
                for tool_call in message.tool_calls
            )

        return chat_schema.MessageSchema(
            role="assistant",
            parts=parts,
            finish_reason=message.response_metadata.get("finish_reason"),
            timestamp=timestamp,
        )

    # 处理 ToolMessage
    elif isinstance(message, ToolMessage):
        parts: list[chat_schema.MessagePart] = []
        attachments: list[chat_schema.Attachment] | None = None

        # 处理 return_file 的工具结果：将文件路径添加到附件中
        if message.name == "return_file" and isinstance(message.content, str):
            try:
                # 解析 JSON 字符串，获取工具调用结果
                payload = json.loads(message.content)
            except json.JSONDecodeError:
                payload = None

            # 检查工具调用结果格式，以及是否成功
            if isinstance(payload, dict) and payload.get("status") == "success":
                # 提取文件路径，转换为附件对象
                f_path = payload.get("f_path")
                if isinstance(f_path, str):
                    attachments = [chat_schema.Attachment(f_path=f_path)]

        return chat_schema.MessageSchema(
            role="tool",
            parts=[
                chat_schema.ToolResultPart(
                    tool_call_id=message.tool_call_id,
                    name=message.name or "",
                    content=str(message.content),
                )
            ],
            attachments=attachments,
            finish_reason=None,
            timestamp=timestamp,
        )

    else:
        return None


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


def _build_image_data_url(
    user_id: int, conversation_id: int, attachment: chat_schema.Attachment
) -> str:
    """读取工作区中的图片附件，并转换为 data URL"""
    # 获取工作区目录
    workspace_dir = get_workspace_dir(user_id, conversation_id).resolve()
    # 获取附件文件路径
    attachment_path = (workspace_dir / attachment.f_path).resolve()
    # 检查路径是否逃逸
    if workspace_dir not in attachment_path.parents:
        raise ValueError(f"Attachment path escapes workspace: {attachment.f_path}")

    # 根据文件名推断 MIME 类型，供 data URL 正确声明图片格式
    mime_type, _ = mimetypes.guess_type(attachment.f_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    # 将图片二进制编码为 base64，并拼接成模型可直接消费的 data URL
    encoded = base64.b64encode(attachment_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _append_prompt(content_parts: list[dict[str, Any]], header: str, lines: list[str]) -> None:
    """向 content_parts 追加提示文本，与已有内容间用换行符分隔"""
    prefix = "\n\n" if content_parts else ""
    content_parts.append(
        chat_schema.TextContent(text=prefix + header + "\n" + "\n".join(lines)).model_dump()
    )


_IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


def _process_attachments(
    content_parts: list[dict[str, Any]],
    attachments: list[chat_schema.Attachment],
    user_id: int | None,
    conversation_id: int | None,
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
            raise ValueError("user_id and conversation_id are required for image attachments")
        lost: list[str] = []
        for a in images:
            try:
                # 将图片转换为 base64 内容，添加到 content_parts
                content_parts.append(
                    chat_schema.ImageContent(
                        image_url=_build_image_data_url(user_id, conversation_id, a)
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


def schema_to_langchain_message(
    message: chat_schema.MessageSchema,
    user_id: int | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    """将 MessageSchema 转换为 LangChain 消息"""
    # 处理工具消息，返回工具调用结果
    if message.role == "tool":
        tool_result = message.parts[0]
        if not isinstance(tool_result, chat_schema.ToolResultPart):
            raise ValueError("Tool message missing ToolResultPart")
        return {
            "role": "tool",
            "tool_call_id": tool_result.tool_call_id,
            "name": tool_result.name,
            "content": tool_result.content,
        }

    # 处理用户或模型消息
    content_parts: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for part in message.parts:
        # 处理文本或图片消息
        if isinstance(part, (chat_schema.TextContent, chat_schema.ImageContent)):
            content_parts.append(part.model_dump())
        # 处理工具调用
        elif isinstance(part, chat_schema.ToolCallPart):
            tool_calls.append(
                {
                    "type": "tool_call",
                    "id": part.tool_call_id,
                    "name": part.name,
                    "args": part.args,
                }
            )

    # 处理用户带附件的消息
    if message.attachments and message.role == "user":
        _process_attachments(content_parts, message.attachments, user_id, conversation_id)

    payload: dict[str, Any] = {"role": message.role, "content": content_parts}
    if tool_calls:
        payload["tool_calls"] = tool_calls

    return payload
