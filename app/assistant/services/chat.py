import asyncio
import json
import mimetypes
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.types import StreamPart
from loguru import logger
from pydantic import ValidationError

from app.assistant.agents.contracts import (
    EVAL_DELEGATIONS_KEY,
    MESSAGE_CREATED_AT_KEY,
    ConversationAgentRuntime,
    DelegationResult,
    EvalDelegationRecord,
    PlannerTurnContext,
    SubagentActivity,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
    build_planner_config,
)
from app.assistant.agents.explorer.semantic_recall_middleware import (
    expand_semantic_recall_messages_for_display,
)
from app.assistant.agents.middleware.user_message_attachments import (
    USER_MESSAGE_ATTACHMENTS_KEY,
    UserMessageAttachment,
    UserMessageAttachments,
)
from app.assistant.agents.middleware.user_message_metadata import (
    USER_MESSAGE_METADATA_KEY,
    UserMessageMetadata,
)
from app.assistant.api.chat import schemas as chat_schema
from app.assistant.services.contracts import (
    AgentRuntimeManager,
    ConversationFileInspector,
)
from app.sandbox.exceptions import SandboxPathError
from app.sandbox.paths import normalize_attachment_path

MESSAGE_PAYLOAD_KEY = "dataagent_message"
_ARTIFACT_DIRECTIVE_PATTERN = re.compile(
    r"^[ ]{0,3}\[\[DATAAGENT_ARTIFACT:(/sessions/[^\r\n]+?)\]\][\t ]*$"
)
_MARKDOWN_FENCE_PATTERN = re.compile(r"^[ ]{0,3}(?P<marker>`{3,}|~{3,})")


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


def _reasoning_content(message: BaseMessage) -> str | None:
    """读取模型供应商返回的完整思考或思考增量"""
    reasoning = message.additional_kwargs.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning:
        return None
    return reasoning


def _text_content(message: BaseMessage) -> str | None:
    """读取模型消息中的正文文本或正文增量"""
    text = "".join(
        part.text
        for part in _content_to_parts(message.content)
        if isinstance(part, chat_schema.TextContent)
    )
    return text or None


def _transform_artifact_directives(
    text: str,
    removable_paths: set[str] | None = None,
) -> tuple[str, list[str]]:
    """查找非代码块独占行指令，并按需移除已验证指令"""
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


def _normalized_directive_path(path: str) -> str:
    """将最终产物指令路径规范化为 Conversation 内相对路径"""
    if not path.startswith("/sessions/"):
        raise SandboxPathError(path)
    normalized = normalize_attachment_path(path.removeprefix("/"))
    if not normalized.startswith("sessions/") or f"/{normalized}" != path:
        raise SandboxPathError(path)
    return normalized


def _is_final_assistant_message(message: BaseMessage) -> bool:
    """判断消息是否可以承载 Planner 最终产物指令"""
    if not isinstance(message, AIMessage) or message.tool_calls:
        return False
    finish_reason = message.response_metadata.get("finish_reason")
    return finish_reason in {None, "stop"}


async def _project_final_artifact_directives(
    message: BaseMessage,
    schema: chat_schema.MessageResponse,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.MessageResponse:
    """把 Planner 最终消息中的有效文件指令投影为附件"""
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
            relative_path = _normalized_directive_path(directive_path)
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


async def _langchain_message_to_schema_with_artifacts(
    message: BaseMessage,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
) -> chat_schema.MessageResponse | None:
    """转换消息，并为 Planner 最终回答解析文件交付指令"""
    schema = _langchain_message_to_schema(message)
    if schema is None:
        return None
    return await _project_final_artifact_directives(
        message,
        schema,
        files,
        user_id,
        conversation_id,
    )


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
        eval_delegations: list[chat_schema.EvalDelegationResponse] | None = None
        raw_eval_delegations = message.additional_kwargs.get(EVAL_DELEGATIONS_KEY)
        if isinstance(raw_eval_delegations, list):
            try:
                records = [
                    EvalDelegationRecord.model_validate(item)
                    for item in raw_eval_delegations
                ]
            except ValidationError:
                logger.warning(
                    f"eval 内部委派元数据无效: message_id={message.id}, "
                    f"tool_call_id={message.tool_call_id}"
                )
            else:
                eval_delegations = [
                    chat_schema.EvalDelegationResponse(
                        delegation_id=record.delegation_id,
                        analysis_id=record.analysis_id,
                        agent_type=record.agent_type,
                        session_id=record.session_id,
                        message=record.message,
                        result=(
                            record.result.model_dump(mode="json")
                            if record.result is not None
                            else None
                        ),
                        attachments=(
                            [
                                chat_schema.Attachment(
                                    f_path=artifact.path.removeprefix("/"),
                                    media_type=artifact.media_type,
                                    description=artifact.description,
                                )
                                for artifact in record.result.artifacts
                            ]
                            if record.result is not None and record.result.artifacts
                            else None
                        ),
                    )
                    for record in records
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
            attachments=_delegation_result_attachments(message) or None,
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
        if reasoning := _reasoning_content(message):
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
    files: ConversationFileInspector,
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
        if schema := await _langchain_message_to_schema_with_artifacts(
            message,
            files,
            user_id,
            conversation_id,
        ):
            result.append(schema)
    return result


async def get_subagent_activity(
    agents: AgentRuntimeManager,
    user_id: int,
    conversation_id: UUID,
    analysis_id: str,
    agent_type: str,
    session_id: str,
    delegation_id: str,
) -> chat_schema.SubagentMessageListResponse | None:
    """读取一次 Specialist delegation 的公开工作消息和状态"""
    runtime = await agents.get_conversation_runtime(user_id, conversation_id)
    activity = await runtime.session_service.get_delegation_activity(
        analysis_id,
        agent_type,
        session_id,
        delegation_id,
    )
    if activity is None:
        return None
    messages = await expand_semantic_recall_messages_for_display(
        activity.messages,
        user_id,
        conversation_id,
    )
    return chat_schema.SubagentMessageListResponse(
        status=activity.status,
        messages=[
            schema
            for message in messages
            if (schema := _langchain_message_to_schema(message)) is not None
        ],
    )


async def _execute_agent(
    input_messages: list[BaseMessage] | None,
    runtime: ConversationAgentRuntime,
    turn_context: PlannerTurnContext,
) -> AsyncGenerator[StreamPart[Any, Any]]:
    """执行 Agent 并流式返回原始更新"""
    config = build_planner_config(
        turn_context.user_id,
        turn_context.conversation_id,
    )
    async for chunk in runtime.planner.astream(
        input={"messages": input_messages} if input_messages is not None else None,
        config=config,
        stream_mode=["updates", "custom", "messages"],
        version="v2",
    ):
        yield chunk


async def _run_agent_turn(
    agents: AgentRuntimeManager,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
    input_messages: list[BaseMessage] | None,
    cancel: asyncio.Event,
) -> AsyncGenerator[chat_schema.ChatStreamEventPayload]:
    """执行新回合或从待执行 Checkpoint 恢复同一回合"""
    runtime = await agents.get_conversation_runtime(user_id, conversation_id)
    async with agents.execution(
        user_id,
        conversation_id,
        runtime=runtime,
    ) as turn_context:
        continuation_count = 0
        thinking_message_ids: set[str] = set()
        text_message_ids: set[str] = set()
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
                        (
                            SubagentMessageActivity,
                            SubagentMessageDeltaActivity,
                            SubagentThinkingDeltaActivity,
                            SubagentStatusActivity,
                        ),
                    ):
                        event = await _subagent_activity_to_event(
                            activity,
                            user_id,
                            conversation_id,
                        )
                        if event is not None:
                            yield event
                    continue
                if chunk.get("type") == "messages":
                    data = chunk.get("data")
                    if not isinstance(data, tuple) or len(data) != 2:
                        continue
                    message, _metadata = data
                    if not isinstance(message, AIMessageChunk):
                        continue
                    reasoning = _reasoning_content(message)
                    if message.id is None:
                        continue
                    message_id = str(message.id)
                    if reasoning is not None:
                        reset = message_id not in thinking_message_ids
                        thinking_message_ids.add(message_id)
                        yield chat_schema.ChatStreamThinkingEvent(
                            type="thinking",
                            message_id=message_id,
                            delta=reasoning,
                            reset=reset,
                        )
                    if text := _text_content(message):
                        reset = message_id not in text_message_ids
                        text_message_ids.add(message_id)
                        yield chat_schema.ChatStreamMessageDeltaEvent(
                            type="message_delta",
                            message_id=message_id,
                            delta=text,
                            reset=reset,
                        )
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
                        if (
                            response
                            := await _langchain_message_to_schema_with_artifacts(
                                message,
                                files,
                                user_id,
                                conversation_id,
                            )
                        ):
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


async def run_agent_turn(
    agents: AgentRuntimeManager,
    files: ConversationFileInspector,
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
    async for event in _run_agent_turn(
        agents,
        files,
        user_id,
        conversation_id,
        [_schema_to_human_message(user_message)],
        cancel,
    ):
        yield event

    logger.info(f"智能体回合结束: conversation_id={conversation_id}")


async def resume_agent_turn(
    agents: AgentRuntimeManager,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
    cancel: asyncio.Event,
) -> AsyncGenerator[chat_schema.ChatStreamEventPayload]:
    """从 Planner 最新 Checkpoint 的待执行任务继续生成"""
    logger.info(f"智能体回合恢复: conversation_id={conversation_id}")
    async for event in _run_agent_turn(
        agents,
        files,
        user_id,
        conversation_id,
        None,
        cancel,
    ):
        yield event
    logger.info(f"智能体回合恢复结束: conversation_id={conversation_id}")


async def can_resume_agent_turn(
    agents: AgentRuntimeManager,
    user_id: int,
    conversation_id: UUID,
) -> bool:
    """检查 Planner 最新 Checkpoint 是否保留待执行任务"""
    runtime = await agents.get_conversation_runtime(user_id, conversation_id)
    state = await runtime.planner.aget_state(
        build_planner_config(user_id, conversation_id)
    )
    return bool(state.next)


class PlannerContinuationLimitError(RuntimeError):
    """Planner 自动续写次数超过服务端硬限制"""

    def __init__(self, max_continuations: int, finish_reason: str) -> None:
        """初始化包含续写上限和结束原因的异常"""
        self.max_continuations = max_continuations
        self.finish_reason = finish_reason
        super().__init__(
            f"规划器在结束原因 {finish_reason!r} 下连续续写次数超过上限 ({max_continuations} 次)"
        )
