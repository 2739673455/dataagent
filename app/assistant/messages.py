"""Assistant 消息、artifact 与流事件投影。"""

from __future__ import annotations

import mimetypes
import re
import uuid
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from loguru import logger

from app.assistant.models import chat as chat_schema
from app.sandbox.errors import SandboxPathError
from app.sandbox.paths import conversation_relative_path

if TYPE_CHECKING:
    from app.sandbox.manager import DockerSandboxManager

_ARTIFACT_DIRECTIVE_PATTERN = re.compile(
    r"^[ ]{0,3}\[\[DATAAGENT_ARTIFACT:(/[^\r\n]+?)\]\][\t ]*$"
)
_MARKDOWN_FENCE_PATTERN = re.compile(r"^[ ]{0,3}(?P<marker>`{3,}|~{3,})")


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
            schema := await langchain_message_to_schema(
                message, files, user_id, conversation_id
            )
        )
        is not None
    ]


async def langchain_message_to_schema(
    message: BaseMessage,
    files: DockerSandboxManager,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.MessageResponse | None:
    """转换消息，为最终回答和 task 结果解析附件。"""
    if isinstance(message, ToolMessage):
        schema = chat_schema.MessageResponse(
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

    else:
        if isinstance(message, AIMessage):
            role: chat_schema.MessageRole = "assistant"
        elif isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, SystemMessage):
            role = "system"
        else:
            return None

        parts: list[chat_schema.MessagePart] = (
            [chat_schema.TextContent(type="text", text=message.text)]
            if message.text
            else []
        )
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

        schema = chat_schema.MessageResponse(
            message_id=message.id,
            role=role,
            parts=parts,
            finish_reason=message.response_metadata.get("finish_reason"),
        )
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


class MessageDelta(TypedDict):
    """一次正文或思考增量及其所属消息的重置标志。"""

    message_id: str
    delta: str
    reset: bool


class MessageDeltaParser:
    """在一次流生命周期内分别跟踪正文和思考，调用方决定事件输出方式。"""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def parse(
        self, data: object
    ) -> Iterator[tuple[Literal["thinking", "text"], MessageDelta]]:
        """忽略非模型增量；同一消息的两类增量分别首次重置。"""
        if not isinstance(data, tuple) or len(data) != 2:
            return
        message, _metadata = data
        if not isinstance(message, AIMessageChunk) or message.id is None:
            return
        message_id = str(message.id)
        parts: tuple[tuple[Literal["thinking", "text"], str | None], ...] = (
            ("thinking", reasoning_text(message)),
            ("text", message.text),
        )
        for kind, text in parts:
            if not text:
                continue
            key = (kind, message_id)
            reset = key not in self._seen
            self._seen.add(key)
            yield kind, MessageDelta(message_id=message_id, delta=text, reset=reset)


def update_messages(data: object) -> Iterator[AIMessage | ToolMessage]:
    """从 Planner 节点更新中提取完整模型与工具消息。"""
    if not isinstance(data, Mapping):
        return
    for node in ("model", "tools"):
        update = data.get(node)
        messages = update.get("messages") if isinstance(update, Mapping) else None
        if isinstance(messages, list):
            yield from (m for m in messages if isinstance(m, AIMessage | ToolMessage))


def reasoning_text(message: BaseMessage) -> str | None:
    """读取 Chat Completions 思考字段或标准思考内容块。"""
    if isinstance(reasoning := message.additional_kwargs.get("reasoning_content"), str):
        return reasoning or None
    return (
        "".join(
            text
            for block in message.content_blocks
            if block.get("type") == "reasoning"
            and isinstance(text := block.get("reasoning"), str)
        )
        or None
    )


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
