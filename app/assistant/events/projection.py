"""Assistant 消息、artifact 与流事件投影。"""

from __future__ import annotations

import json
import mimetypes
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict, cast
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

from app.assistant.agents.middleware.message_context import (
    USER_MESSAGE_CONTEXT_KEY,
    UserMessageAttachment,
    UserMessageContext,
    read_user_message_context,
)
from app.assistant.events import schemas as chat_schema
from app.assistant.events.content import (
    is_final_assistant_message,
    normalize_finish_reason,
    normalized_content_blocks,
    reasoning_text,
)
from app.assistant.execution.types import (
    MESSAGE_CREATED_AT_KEY,
    SubagentActivity,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
)
from app.shared.contracts.analysis import AgentType

if TYPE_CHECKING:
    from app.sandbox import DockerSandboxManager

_ARTIFACT_DIRECTIVE_PATTERN = re.compile(
    r"^[ ]{0,3}\[\[DATAAGENT_ARTIFACT:(/[^\r\n]+?)\]\][\t ]*$"
)
_MARKDOWN_FENCE_PATTERN = re.compile(r"^[ ]{0,3}(?P<marker>`{3,}|~{3,})")


def _message_created_at(message: BaseMessage) -> datetime | None:
    """读取消息创建时间。"""
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
    """将 LangChain 消息内容转换为接口消息片段。"""
    parts: list[chat_schema.MessagePart] = []
    for item in normalized_content_blocks(content):
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


def _artifact_directives(text: str) -> list[tuple[int, int, str]]:
    """一次扫描全文，记录代码围栏外独占行指令的位置。"""
    directives: list[tuple[int, int, str]] = []
    fence: str | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if match := _MARKDOWN_FENCE_PATTERN.match(content):
            marker = match.group("marker")
            if fence is None:
                fence = marker
            elif (
                marker[0] == fence[0]
                and len(marker) >= len(fence)
                and not content[match.end() :].strip()
            ):
                fence = None
        elif fence is None and (
            match := _ARTIFACT_DIRECTIVE_PATTERN.fullmatch(content)
        ):
            directives.append((offset, offset + len(line), match.group(1)))
        offset += len(line)
    return directives


async def _project_artifact_directives(
    schema: chat_schema.MessageResponse,
    files: DockerSandboxManager,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.MessageResponse:
    """把统一文件指令投影为附件，保留无效指令及代码示例。"""

    # 文本块可以在围栏或指令中间断开；合并扫描，输出仍保留原块顺序。
    directives = _artifact_directives(
        "".join(
            part.text
            for part in schema.parts
            if isinstance(part, chat_schema.TextContent)
        )
    )
    if not directives:
        return schema
    candidate_paths = [path for _, _, path in directives]

    accepted_paths: set[str] = set()
    attachments: list[chat_schema.Attachment] = []
    seen_paths: set[str] = set()
    resolved = await files.resolve_artifacts(user_id, conversation_id, candidate_paths)
    for directive_path in dict.fromkeys(candidate_paths):
        artifact = resolved.get(directive_path)
        if artifact is None:
            logger.warning(
                "最终产物指令无效或文件不可下载: "
                f"conversation_id={conversation_id}, path={directive_path!r}"
            )
            continue
        relative_path = artifact.relative_path
        accepted_paths.add(directive_path)
        if relative_path in seen_paths:
            continue
        seen_paths.add(relative_path)
        media_type, _ = mimetypes.guess_type(relative_path)
        attachments.append(
            chat_schema.Attachment(
                f_path=relative_path,
                media_type=media_type,
            )
        )

    if not accepted_paths:
        return schema

    parts: list[chat_schema.MessagePart] = []
    offset = 0
    for part in schema.parts:
        if not isinstance(part, chat_schema.TextContent):
            parts.append(part)
            continue
        end = offset + len(part.text)
        cursor = offset
        kept: list[str] = []
        for start, stop, path in directives:
            if path not in accepted_paths or stop <= offset or start >= end:
                continue
            kept.append(part.text[cursor - offset : max(start, offset) - offset])
            cursor = min(stop, end)
        kept.append(part.text[cursor - offset :])
        cleaned = "".join(kept)
        if cleaned:
            parts.append(part.model_copy(update={"text": cleaned}))
        offset = end
    return schema.model_copy(
        update={
            "parts": parts,
            "attachments": attachments or None,
        }
    )


async def _project_delegation_result(
    result: dict[str, object] | None,
    files: DockerSandboxManager,
    user_id: int,
    conversation_id: UUID,
) -> tuple[dict[str, object] | None, list[chat_schema.Attachment]]:
    """以相同文件协议展示委派文本，原始工具结果和 Checkpoint 不变。"""
    if result is None or not isinstance(content := result.get("content"), str):
        return result, []
    projected = await _project_artifact_directives(
        chat_schema.MessageResponse(
            role="assistant", parts=[chat_schema.TextContent(type="text", text=content)]
        ),
        files,
        user_id,
        conversation_id,
    )
    return {
        **result,
        "content": "".join(
            part.text
            for part in projected.parts
            if isinstance(part, chat_schema.TextContent)
        ),
    }, projected.attachments or []


async def langchain_message_to_schema(
    message: BaseMessage,
    files: DockerSandboxManager,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.MessageResponse | None:
    """将 LangChain 消息转换为接口消息。"""
    if isinstance(message, ToolMessage):
        # ToolMessage 使用工具结果协议，不走普通文本消息的投影。
        content = str(message.content)
        attachments = []
        if message.name == "delegation" and isinstance(message.content, str):
            try:
                payload = json.loads(message.content)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                result, attachments = await _project_delegation_result(
                    payload,
                    files,
                    user_id,
                    conversation_id,
                )
                content = json.dumps(result, ensure_ascii=False)
        return chat_schema.MessageResponse(
            message_id=message.id,
            created_at=_message_created_at(message),
            role="tool",
            parts=[
                chat_schema.ToolResultPart(
                    type="tool_result",
                    tool_call_id=message.tool_call_id,
                    name=message.name or "",
                    content=content,
                )
            ],
            attachments=attachments or None,
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

    # UserMessageContext 是 Checkpoint 私有状态，API 只公开其中的时间和附件引用。
    context = (
        read_user_message_context(message)
        if isinstance(message, HumanMessage)
        else None
    )
    schema = chat_schema.MessageResponse(
        message_id=message.id,
        created_at=(
            context.received_at if context is not None else _message_created_at(message)
        ),
        role=role,
        parts=parts,
        attachments=(
            [chat_schema.Attachment(f_path=item.f_path) for item in context.attachments]
            if context is not None and context.attachments
            else None
        ),
        finish_reason=normalize_finish_reason(
            message.response_metadata.get("finish_reason")
        ),
    )

    if is_final_assistant_message(message):
        return await _project_artifact_directives(
            schema, files, user_id, conversation_id
        )
    return schema


class _SubagentEventContext(TypedDict):
    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str


async def subagent_activity_to_event(
    activity: SubagentActivity,
    user_id: int,
    conversation_id: UUID,
    *,
    files: DockerSandboxManager,
) -> chat_schema.ChatStreamEventPayload | None:
    """把受信任的 Agent 内部活动投影为公开聊天事件。"""
    common = _SubagentEventContext(
        delegation_id=activity.delegation_id,
        analysis_id=activity.analysis_id,
        agent_type=activity.agent_type,
        session_id=activity.session_id,
    )
    if isinstance(activity, SubagentMessageActivity):
        message = await langchain_message_to_schema(
            activity.message, files, user_id, conversation_id
        )
        if message is None:
            return None
        return chat_schema.ChatStreamSubagentMessageEvent(
            **common,
            type="subagent_message",
            message=message,
        )
    if isinstance(activity, SubagentThinkingDeltaActivity):
        return chat_schema.ChatStreamSubagentThinkingEvent(
            **common,
            type="subagent_thinking",
            message_id=activity.message_id,
            delta=activity.delta,
            reset=activity.reset,
        )
    if isinstance(activity, SubagentMessageDeltaActivity):
        return chat_schema.ChatStreamSubagentMessageDeltaEvent(
            **common,
            type="subagent_message_delta",
            message_id=activity.message_id,
            delta=activity.delta,
            reset=activity.reset,
        )
    if isinstance(activity, SubagentStatusActivity):
        return chat_schema.ChatStreamSubagentStatusEvent(
            **common,
            type="subagent_status",
            status=activity.status,
        )
    return None


def schema_to_human_message(
    message: chat_schema.UserMessageRequest,
) -> HumanMessage:
    """将用户消息转换为 LangChain 消息。"""
    content_parts = [part.model_dump() for part in message.parts]

    received_at = datetime.now(UTC)
    context = UserMessageContext(
        received_at=received_at,
        attachments=[
            UserMessageAttachment(f_path=attachment.f_path)
            for attachment in message.attachments or ()
        ],
    )
    return HumanMessage(
        id=str(uuid.uuid4()),
        content=cast(list[str | dict[Any, Any]], content_parts),
        additional_kwargs={
            USER_MESSAGE_CONTEXT_KEY: context.model_dump(mode="json"),
        },
    )
