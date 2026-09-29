"""Assistant 消息、artifact 与流事件投影。"""

from __future__ import annotations

import mimetypes
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
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

from app.assistant.messages.content import normalized_content_blocks, reasoning_text
from app.assistant.models import chat as chat_schema
from app.sandbox.errors import SandboxPathError
from app.sandbox.paths import conversation_relative_path

if TYPE_CHECKING:
    from app.sandbox.manager import DockerSandboxManager

_ARTIFACT_DIRECTIVE_PATTERN = re.compile(
    r"^[ ]{0,3}\[\[DATAAGENT_ARTIFACT:(/[^\r\n]+?)\]\][\t ]*$"
)
_MARKDOWN_FENCE_PATTERN = re.compile(r"^[ ]{0,3}(?P<marker>`{3,}|~{3,})")


def _content_to_parts(content: Any) -> list[chat_schema.MessagePart]:
    """将 LangChain 消息内容转换为接口消息片段。"""
    parts: list[chat_schema.MessagePart] = []
    for item in normalized_content_blocks(content):
        match item:
            case str(text):
                parts.append(chat_schema.TextContent(type="text", text=text))
            case {"type": "text" | "input_text" | "output_text", "text": str(text)}:
                parts.append(chat_schema.TextContent(type="text", text=text))
            case {"type": "image_url", "image_url": image_url}:
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if isinstance(url, str):
                    parts.append(
                        chat_schema.ImageContent(type="image_url", image_url=url)
                    )
    return parts


def _transform_artifact_directives(
    text: str,
    removable_paths: set[str] | None = None,
) -> tuple[str, list[str]]:
    """查找非代码块独占行指令，并按需移除已验证指令。"""
    paths: list[str] = []
    output: list[str] = []
    fence_character: str | None = None
    fence_length = 0

    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        fence_match = _MARKDOWN_FENCE_PATTERN.match(content)
        if fence_match is not None:
            marker = fence_match.group("marker")
            if fence_character is None:
                fence_character, fence_length = marker[0], len(marker)
            elif (
                marker[0] == fence_character
                and len(marker) >= fence_length
                and not content[fence_match.end() :].strip()
            ):
                fence_character = None
            output.append(line)
            continue

        if fence_character is None:
            directive_match = _ARTIFACT_DIRECTIVE_PATTERN.fullmatch(content)
            if directive_match is not None:
                path = directive_match.group(1)
                paths.append(path)
                if removable_paths is not None and path in removable_paths:
                    continue
        output.append(line)

    return "".join(output), paths


async def _resolve_artifacts(
    texts: Iterable[str],
    files: DockerSandboxManager,
    user_id: int,
    conversation_id: UUID,
) -> tuple[set[str], list[chat_schema.Attachment]]:
    """解析文本中的文件指令，验证路径与下载资格并去重。"""
    candidate_paths = (
        path for text in texts for path in _transform_artifact_directives(text)[1]
    )
    accepted_paths: set[str] = set()
    attachments: dict[str, chat_schema.Attachment] = {}
    for directive_path in candidate_paths:
        try:
            relative_path = conversation_relative_path(
                directive_path,
                conversation_id,
            )
        except SandboxPathError:
            logger.warning(
                "最终产物指令路径无效: "
                f"conversation_id={conversation_id}, path={directive_path!r}"
            )
            continue
        if relative_path in attachments:
            accepted_paths.add(directive_path)
            continue
        try:
            downloadable = await files.is_downloadable_file(
                user_id,
                conversation_id,
                relative_path,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "检查最终产物指令文件失败: "
                f"conversation_id={conversation_id}, path={relative_path!r}"
            )
            continue
        if not downloadable:
            logger.warning(
                "最终产物指令文件不可下载: "
                f"conversation_id={conversation_id}, path={relative_path!r}"
            )
            continue
        accepted_paths.add(directive_path)
        attachments[relative_path] = chat_schema.Attachment(
            f_path=relative_path,
            media_type=mimetypes.guess_type(relative_path)[0],
        )

    return accepted_paths, list(attachments.values())


async def langchain_message_to_schema_with_artifacts(
    message: BaseMessage,
    files: DockerSandboxManager,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.MessageResponse | None:
    """转换消息，为最终回答和 task 结果解析附件。"""
    schema = langchain_message_to_schema(message)
    if schema is None:
        return None
    is_task_result = (
        isinstance(message, ToolMessage)
        and message.name == "task"
        and isinstance(message.content, str)
    )
    is_final_answer = (
        isinstance(message, AIMessage)
        and not message.tool_calls
        and schema.finish_reason in {None, "stop"}
    )
    if not (is_task_result or is_final_answer):
        return schema
    texts = (
        [cast(str, message.content)]
        if is_task_result
        else [
            part.text
            for part in schema.parts
            if isinstance(part, chat_schema.TextContent)
        ]
    )
    accepted_paths, attachments = await _resolve_artifacts(
        texts, files, user_id, conversation_id
    )
    if not accepted_paths:
        return schema
    schema.attachments = attachments
    if is_final_answer:
        for part in schema.parts:
            if isinstance(part, chat_schema.TextContent):
                part.text = _transform_artifact_directives(part.text, accepted_paths)[0]
        schema.parts = [
            part
            for part in schema.parts
            if not isinstance(part, chat_schema.TextContent) or part.text
        ]
    return schema


async def project_messages(
    messages: Iterable[object],
    files: DockerSandboxManager,
    user_id: int,
    conversation_id: UUID,
) -> list[chat_schema.MessageResponse]:
    """统一投影历史与流式更新中的完整消息，包括思考、工具结果和附件。"""
    return [
        schema
        for message in messages
        if isinstance(message, BaseMessage)
        and (
            schema := await langchain_message_to_schema_with_artifacts(
                message, files, user_id, conversation_id
            )
        )
        is not None
    ]


def langchain_message_to_schema(
    message: BaseMessage,
) -> chat_schema.MessageResponse | None:
    """将 LangChain 消息转换为接口消息。"""
    if isinstance(message, ToolMessage):
        return chat_schema.MessageResponse(
            message_id=message.id,
            role="tool",
            parts=[
                chat_schema.ToolResultPart(
                    type="tool_result",
                    tool_call_id=message.tool_call_id,
                    name=message.name or "",
                    content=str(message.content),
                )
            ],
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
        # 部分 Provider 把 reasoning 放在 content 之外；统一放到正文前且只投影一次。
        if reasoning := reasoning_text(message):
            parts.insert(
                0,
                chat_schema.ThinkingContent(
                    type="thinking",
                    text=reasoning,
                    status="complete",
                ),
            )
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
        role=role,
        parts=parts,
        finish_reason=message.response_metadata.get("finish_reason"),
    )


def schema_to_human_message(
    message: chat_schema.UserMessageRequest,
) -> HumanMessage:
    """转换用户消息并记录带时区的接收时间，作为模型的时间基准。"""
    content_parts = [part.model_dump() for part in message.parts]

    return HumanMessage(
        id=str(uuid.uuid4()),
        content=cast(list[str | dict[Any, Any]], content_parts),
        additional_kwargs={"received_at": datetime.now(UTC).isoformat()},
    )
