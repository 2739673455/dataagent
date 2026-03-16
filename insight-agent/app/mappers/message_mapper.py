import base64
import json
import mimetypes
from datetime import datetime
from typing import Any, cast

from app.core.agent import get_workspace_dir
from app.entities.chat import Message
from app.schemas import chat_schema
from langchain.messages import AIMessage, ToolMessage
from loguru import logger


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
    attachments = (
        [chat_schema.Attachment(**item) for item in json.loads(message.attachments)]
        if message.attachments
        else None
    )

    return chat_schema.MessageSchema(
        message_id=message.id,
        context_seq=message.context_seq,
        role=cast(chat_schema.MessageRole, message.role),
        parts=parts,
        attachments=attachments,
        finish_reason=cast(chat_schema.FinishReason | None, message.finish_reason),
        timestamp=message.create_at,
    )


def schema_to_entity(
    message: chat_schema.MessageSchema, conversation_id: int
) -> Message:
    """将 MessageSchema 转换为消息实体"""
    # 检查是否有上下文顺序号
    if message.context_seq is None:
        raise ValueError("Message context_seq is required")

    # 将消息片段对象转换为 json 字符串
    parts = json.dumps(
        [part.model_dump() for part in message.parts], ensure_ascii=False
    )

    # 将附件对象转换为 json 字符串
    attachments = (
        json.dumps(
            [attachment.model_dump() for attachment in message.attachments],
            ensure_ascii=False,
        )
        if message.attachments is not None
        else None
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


def _build_image_data_url(
    user_id: int, conversation_id: int, attachment: chat_schema.Attachment
) -> str:
    """读取工作区中的图片附件，并转换为 data URL"""
    # 仅允许读取当前会话工作区下的附件文件，避免路径逃逸
    workspace_dir = get_workspace_dir(user_id, conversation_id).resolve()
    attachment_path = (workspace_dir / attachment.path).resolve()
    if workspace_dir not in attachment_path.parents:
        raise ValueError(f"Attachment path escapes workspace: {attachment.path}")

    # 根据文件名推断 MIME 类型，供 data URL 正确声明图片格式
    mime_type, _ = mimetypes.guess_type(attachment.path)
    if not mime_type:
        mime_type = "application/octet-stream"

    # 将图片二进制编码为 base64，并拼接成模型可直接消费的 data URL
    encoded = base64.b64encode(attachment_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def langchain_message_to_schema(
    message: AIMessage | ToolMessage,
) -> chat_schema.MessageSchema | None:
    """将 LangChain 消息转换为 MessageSchema，同时添加时间戳"""
    timestamp = datetime.now()

    # 处理 AIMessage
    if isinstance(message, AIMessage):
        # 转换 content 与 tool_calls 为消息片段对象
        content = message.content
        assert isinstance(content, str), "AI message content is not string"
        parts: list[chat_schema.MessagePart] = [
            chat_schema.TextContent(text=content),
            *[
                chat_schema.ToolCallPart(
                    tool_call_id=tool_call.get("id") or "",
                    name=tool_call.get("name") or "",
                    args=tool_call.get("args", {}),
                )
                for tool_call in message.tool_calls
            ],
        ]
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

        # 处理 return_file 的工具结果
        if message.name == "return_file":
            if isinstance(message.content, str):
                try:
                    payload = json.loads(message.content)
                except json.JSONDecodeError:
                    payload = None

                if isinstance(payload, dict) and payload.get("status") == "success":
                    path = payload.get("path")
                    raw_name = payload.get("raw_name")
                    if isinstance(path, str) and isinstance(raw_name, str):
                        attachments = [
                            chat_schema.Attachment(raw_name=raw_name, path=path)
                        ]

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
    """将 Agent 流式输出块转换为 MessageSchema 列表"""
    schemas: list[chat_schema.MessageSchema] = []

    # 处理 model 和 tools 两类节点的返回消息
    for key in ("model", "tools"):
        if (
            (key in chunk)
            and (messages := chunk[key].get("messages"))
            and (isinstance(messages, list))
        ):
            for message in messages:
                if schema := langchain_message_to_schema(message):
                    schemas.append(schema)

    return schemas


def schema_to_langchain_message(
    message: chat_schema.MessageSchema,
    user_id: int | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    """将 MessageSchema 转换为 LangChain 消息"""

    # 工具消息
    if message.role == "tool":
        tool_result = next(
            part
            for part in message.parts
            if isinstance(part, chat_schema.ToolResultPart)
        )
        return {
            "role": "tool",
            "tool_call_id": tool_result.tool_call_id,
            "name": tool_result.name,
            "content": tool_result.content,
        }

    # 用户或模型消息
    content_parts: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, (chat_schema.TextContent, chat_schema.ImageContent)):
            content_parts.append(part.model_dump())
        elif isinstance(part, chat_schema.ToolCallPart):
            tool_calls.append(
                {
                    "type": "tool_call",
                    "id": part.tool_call_id,
                    "name": part.name,
                    "args": part.args,
                }
            )

    # 处理带附件的消息
    if message.attachments and message.role == "user":
        # 图片附件
        image_attachments = []
        # 文件附件
        document_attachments = []

        for attachment in message.attachments:
            # 获取文件后缀
            suffix = (
                attachment.path.rsplit(".", 1)[-1].lower()
                if "." in attachment.path
                else ""
            )
            # 判断文件后缀是否为图片
            if suffix in {"png", "jpg", "jpeg", "gif", "webp", "bmp"}:
                image_attachments.append(attachment)
            else:
                document_attachments.append(attachment)

        # 添加文件附件提示
        if document_attachments:
            # 用户消息，提示用户上传过文件
            file_prompt = "用户上传的以下文件已保存到当前工作区，可直接读取："
            if content_parts:
                file_prompt = f"\n\n{file_prompt}"
            # 拼接附件信息
            attachment_lines = [
                file_prompt,
                *[
                    f"- 原始文件名：`{attachment.raw_name}`，工作区相对路径：`{attachment.path}`"
                    for attachment in document_attachments
                ],
            ]
            content_parts.append(
                chat_schema.TextContent(text="\n".join(attachment_lines)).model_dump()
            )

        # 添加图片附件提示
        if image_attachments:
            # 如果缺少 user_id 或 conversation_id，则报错
            if user_id is None or conversation_id is None:
                raise ValueError(
                    "user_id and conversation_id are required for image attachments"
                )

            image_loss_list = []
            for attachment in image_attachments:
                try:
                    # 将图片转换为 base64 内容
                    content_parts.append(
                        chat_schema.ImageContent(
                            image_url=_build_image_data_url(
                                user_id, conversation_id, attachment
                            )
                        ).model_dump()
                    )
                except OSError:
                    # 如果图片文件不存在，在 prompt 中添加提示
                    logger.warning(
                        f"Attachment image is unavailable: conversation_id={conversation_id}, file={attachment.path}"
                    )
                    image_loss_list.append(
                        f"- 原始文件名：`{attachment.raw_name}`，工作区路径：`{attachment.path}`"
                    )
            if image_loss_list:
                image_loss_prompt = "用户之前上传了一张图片，但该文件当前已不存在："
                if content_parts:
                    image_loss_prompt = f"\n\n{image_loss_prompt}"
                image_loss_prompt += "\n".join(image_loss_list)
                content_parts.append(
                    chat_schema.TextContent(text=image_loss_prompt).model_dump()
                )

    payload: dict[str, Any] = {"role": message.role, "content": content_parts}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload
