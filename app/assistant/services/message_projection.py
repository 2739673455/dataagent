"""Assistant 消息、artifact 与流事件投影。"""

import json
import mimetypes
import re
import uuid
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

from app.assistant.agents.contracts import (
    EVAL_DELEGATIONS_KEY,
    MESSAGE_CREATED_AT_KEY,
    SubagentActivity,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
)
from app.assistant.agents.middleware.semantic_recall_expansion import (
    expand_semantic_recall_messages_for_display,
)
from app.assistant.agents.middleware.user_message_context import (
    USER_MESSAGE_CONTEXT_KEY,
    UserMessageAttachment,
    UserMessageContext,
    read_user_message_context,
)
from app.assistant.contracts import chat as chat_schema
from app.assistant.message_content import normalized_content_blocks, reasoning_text
from app.assistant.services.contracts import (
    ConversationFileInspector,
)
from app.sandbox.exceptions import SandboxPathError
from app.sandbox.paths import conversation_relative_path

_KNOWN_FINISH_REASONS = (
    "content_filter",
    "function_call",
    "tool_calls",
    "length",
    "stop",
)
_ARTIFACT_DIRECTIVE_PATTERN = re.compile(
    r"^[ ]{0,3}\[\[DATAAGENT_ARTIFACT:(/[^\r\n]+?)\]\][\t ]*$"
)
_MARKDOWN_FENCE_PATTERN = re.compile(r"^[ ]{0,3}(?P<marker>`{3,}|~{3,})")


def normalize_finish_reason(value: object) -> str | None:
    """还原流式消息元数据中被重复拼接的已知结束原因。

    LangChain 合并流式 Chunk 时会拼接重复出现的字符串元数据，例如两个
    ``stop`` 可能变成 ``stopstop``。未知供应商值保持原样，避免掩盖新状态。
    """
    if not isinstance(value, str):
        return None
    for reason in _KNOWN_FINISH_REASONS:
        repeat_count, remainder = divmod(len(value), len(reason))
        if repeat_count > 1 and remainder == 0 and value == reason * repeat_count:
            return reason
    return value


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
        if fence_character is None:
            if fence_match is not None:
                marker = fence_match.group("marker")
                fence_character = marker[0]
                fence_length = len(marker)
                output.append(line)
                continue
        elif fence_match is not None:
            marker = fence_match.group("marker")
            suffix = content[fence_match.end() :]
            if (
                marker[0] == fence_character
                and len(marker) >= fence_length
                and not suffix.strip()
            ):
                fence_character = None
                fence_length = 0
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


def _normalized_directive_path(path: str, conversation_id: UUID) -> str:
    """将最终产物指令路径规范化为 Conversation 内相对路径。"""
    normalized = conversation_relative_path(path, conversation_id)
    if not normalized.startswith("sessions/"):
        raise SandboxPathError(path)
    return normalized


def _is_final_assistant_message(message: BaseMessage) -> bool:
    """判断消息是否可以承载 Planner 最终产物指令。"""
    if not isinstance(message, AIMessage) or message.tool_calls:
        return False
    finish_reason = normalize_finish_reason(
        message.response_metadata.get("finish_reason")
    )
    return finish_reason in {None, "stop"}


async def _project_final_artifact_directives(
    message: BaseMessage,
    schema: chat_schema.MessageResponse,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.MessageResponse:
    """把 Planner 最终消息中的有效文件指令投影为附件。"""
    if not _is_final_assistant_message(message):
        return schema

    candidate_paths: list[str] = []
    for part in schema.parts:
        if isinstance(part, chat_schema.TextContent):
            _, paths = _transform_artifact_directives(part.text)
            candidate_paths.extend(paths)
    if not candidate_paths:
        return schema

    accepted_paths: set[str] = set()
    attachments: list[chat_schema.Attachment] = []
    seen_paths: set[str] = set()
    for directive_path in candidate_paths:
        try:
            relative_path = _normalized_directive_path(
                directive_path,
                conversation_id,
            )
        except SandboxPathError:
            logger.warning(
                "最终产物指令路径无效: "
                f"conversation_id={conversation_id}, path={directive_path!r}"
            )
            continue
        if relative_path in seen_paths:
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
        seen_paths.add(relative_path)
        accepted_paths.add(directive_path)
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
    for part in schema.parts:
        if not isinstance(part, chat_schema.TextContent):
            parts.append(part)
            continue
        cleaned, _ = _transform_artifact_directives(part.text, accepted_paths)
        if cleaned:
            parts.append(part.model_copy(update={"text": cleaned}))
    return schema.model_copy(
        update={
            "parts": parts,
            "attachments": attachments or None,
        }
    )


async def langchain_message_to_schema_with_artifacts(
    message: BaseMessage,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.MessageResponse | None:
    """转换消息，并为 Planner 最终回答解析文件交付指令。"""
    schema = langchain_message_to_schema(message, conversation_id)
    if schema is None:
        return None
    return await _project_final_artifact_directives(
        message,
        schema,
        files,
        user_id,
        conversation_id,
    )


def _delegation_result_attachments(
    message: ToolMessage,
    conversation_id: UUID,
) -> list[chat_schema.Attachment]:
    """从委派结果的稳定协议中提取可下载产物。"""
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
    return _artifact_attachments(cast(dict[str, object], payload), conversation_id)


def _artifact_attachments(
    result: dict[str, object] | None,
    conversation_id: UUID,
) -> list[chat_schema.Attachment]:
    """从受控委派结果的产物载荷投影可下载附件。"""
    if result is None:
        return []
    attachments: list[chat_schema.Attachment] = []
    for artifact in cast(list[dict[str, object]], result.get("artifacts", [])):
        artifact_path = cast(str, artifact["path"])
        try:
            path = conversation_relative_path(artifact_path, conversation_id)
        except SandboxPathError:
            logger.warning(
                f"委派结果产物路径超出当前 Conversation: path={artifact_path!r}"
            )
            continue
        attachments.append(
            chat_schema.Attachment(
                f_path=path,
                media_type=cast(str | None, artifact.get("media_type")),
                description=cast(str | None, artifact.get("description")),
            )
        )
    return attachments


def langchain_message_to_schema(
    message: BaseMessage,
    conversation_id: UUID,
) -> chat_schema.MessageResponse | None:
    """将 LangChain 消息转换为接口消息。"""
    if isinstance(message, ToolMessage):
        # ToolMessage 有独立的公开协议，还可能携带 eval 内部委派记录，不能走
        # 普通文本消息的 content blocks 投影。
        eval_delegations: list[chat_schema.EvalDelegationResponse] | None = None
        raw_eval_delegations = message.additional_kwargs.get(EVAL_DELEGATIONS_KEY)
        if isinstance(raw_eval_delegations, list):
            eval_delegations = [
                chat_schema.EvalDelegationResponse.model_construct(
                    **cast(Any, record),
                    attachments=_artifact_attachments(
                        cast(dict[str, object] | None, record.get("result")),
                        conversation_id,
                    )
                    or None,
                )
                for record in cast(list[dict[str, object]], raw_eval_delegations)
            ]
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
            attachments=(
                _delegation_result_attachments(message, conversation_id) or None
            ),
            eval_delegations=eval_delegations,
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
    return chat_schema.MessageResponse(
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


async def subagent_activity_to_event(
    activity: SubagentActivity,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.ChatStreamEventPayload | None:
    """把受信任的 Agent 内部活动投影为公开聊天事件。"""
    if isinstance(activity, SubagentMessageActivity):
        expanded = await expand_semantic_recall_messages_for_display(
            [activity.message],
            user_id,
            conversation_id,
        )
        message = langchain_message_to_schema(expanded[0], conversation_id)
        if message is None:
            return None
        return chat_schema.ChatStreamSubagentMessageEvent(
            type="subagent_message",
            delegation_id=activity.delegation_id,
            analysis_id=activity.analysis_id,
            agent_type=activity.agent_type,
            session_id=activity.session_id,
            message=message,
            parent_tool_call_id=activity.parent_tool_call_id,
            instruction=activity.instruction,
        )
    if isinstance(activity, SubagentThinkingDeltaActivity):
        return chat_schema.ChatStreamSubagentThinkingEvent(
            type="subagent_thinking",
            delegation_id=activity.delegation_id,
            analysis_id=activity.analysis_id,
            agent_type=activity.agent_type,
            session_id=activity.session_id,
            message_id=activity.message_id,
            delta=activity.delta,
            reset=activity.reset,
            parent_tool_call_id=activity.parent_tool_call_id,
            instruction=activity.instruction,
        )
    if isinstance(activity, SubagentMessageDeltaActivity):
        return chat_schema.ChatStreamSubagentMessageDeltaEvent(
            type="subagent_message_delta",
            delegation_id=activity.delegation_id,
            analysis_id=activity.analysis_id,
            agent_type=activity.agent_type,
            session_id=activity.session_id,
            message_id=activity.message_id,
            delta=activity.delta,
            reset=activity.reset,
            parent_tool_call_id=activity.parent_tool_call_id,
            instruction=activity.instruction,
        )
    if isinstance(activity, SubagentStatusActivity):
        return chat_schema.ChatStreamSubagentStatusEvent(
            type="subagent_status",
            delegation_id=activity.delegation_id,
            analysis_id=activity.analysis_id,
            agent_type=activity.agent_type,
            session_id=activity.session_id,
            status=activity.status,
            parent_tool_call_id=activity.parent_tool_call_id,
            instruction=activity.instruction,
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
