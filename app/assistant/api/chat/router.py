"""对话管理、语义召回与 Agent SSE 流式交互路由。"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.assistant import contracts as chat_schema
from app.assistant.api.chat.dependencies import (
    ConversationServiceDep,
    ConversationTurnServiceDep,
)
from app.assistant.contracts.base import Identifier
from app.dependencies import AnalysisUserDep, CurrentUserDep
from app.shared.contracts.analysis import AgentType
from app.shared.observability import context

router = APIRouter(tags=["chat"])
_SSE_HEARTBEAT_SECONDS = 15


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def api_create_conversation(
    body: chat_schema.CreateConversationRequest,
    conversations: ConversationServiceDep,
    current_user: AnalysisUserDep,
) -> chat_schema.ConversationResponse:
    """创建新对话。"""
    return await conversations.create(current_user.id, body)


@router.post("/delete")
async def api_delete_conversations(
    body: chat_schema.DeleteConversationRequest,
    conversations: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> None:
    """删除对话。"""
    return await conversations.delete(current_user.id, body)


@router.delete(
    "/draft/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def api_delete_draft_conversation(
    conversation_id: UUID,
    conversations: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> Response:
    """幂等删除当前用户主动放弃的草稿会话。"""
    await conversations.delete_draft(current_user.id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/update")
async def api_update_conversation(
    body: chat_schema.UpdateConversationRequest,
    conversations: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> None:
    """修改对话信息。"""
    return await conversations.rename(current_user.id, body)


@router.get("/ls")
async def api_get_conversations(
    conversations: ConversationServiceDep, current_user: CurrentUserDep
) -> chat_schema.ConversationListResponse:
    """获取所有对话。"""
    return await conversations.list(current_user.id)


@router.get("/ls/{conversation_id}")
async def api_get_messages(
    conversation_id: UUID,
    conversations: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> chat_schema.MessageListResponse:
    """从 LangGraph 状态获取某个对话的所有消息。"""
    return await conversations.messages(current_user.id, conversation_id)


@router.get(
    "/{conversation_id}/subagents/{analysis_id}/{agent_type}/{session_id}/"
    "runs/{delegation_id}/messages"
)
async def api_get_subagent_messages(
    conversation_id: UUID,
    analysis_id: Identifier,
    agent_type: AgentType,
    session_id: Identifier,
    delegation_id: str,
    conversations: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> chat_schema.SubagentMessageListResponse:
    """读取一次 Specialist delegation 的公开工作消息。"""
    return await conversations.delegation_messages(
        current_user.id,
        conversation_id,
        analysis_id,
        agent_type,
        session_id,
        delegation_id,
    )


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "model": chat_schema.ChatStreamEvent,
            "description": "每个 SSE data 帧均为 ChatStreamEvent JSON",
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/ChatStreamEvent"}
                }
            },
        }
    },
)
async def api_stream_chat(
    body: chat_schema.ChatStreamRequest,
    turns: ConversationTurnServiceDep,
    current_user: AnalysisUserDep,
) -> StreamingResponse:
    """启动后台对话回合并订阅 Agent 事件。"""
    user_id = current_user.id
    context.user_id_ctx.set(str(user_id))
    events = await turns.start(user_id, body.conversation_id, body.message)
    return _sse_response(body.conversation_id, events)


@router.post("/{conversation_id}/resume", response_class=StreamingResponse)
async def api_resume_chat(
    conversation_id: UUID,
    turns: ConversationTurnServiceDep,
    current_user: AnalysisUserDep,
) -> StreamingResponse:
    """从中断的 Planner Checkpoint 继续当前用户回合。"""
    user_id = current_user.id
    context.user_id_ctx.set(str(user_id))
    events = await turns.resume(user_id, conversation_id)
    return _sse_response(conversation_id, events)


@router.get("/{conversation_id}/run")
async def api_get_conversation_run_status(
    conversation_id: UUID,
    turns: ConversationTurnServiceDep,
    current_user: AnalysisUserDep,
) -> chat_schema.ConversationRunStatusResponse:
    """查询 Conversation 是否有正在后台执行的 Planner Run。"""
    return await turns.status(current_user.id, conversation_id)


@router.get("/{conversation_id}/events", response_class=StreamingResponse)
async def api_subscribe_conversation_run(
    conversation_id: UUID,
    turns: ConversationTurnServiceDep,
    current_user: AnalysisUserDep,
) -> StreamingResponse:
    """订阅已经启动的后台 Planner Run。"""
    context.user_id_ctx.set(str(current_user.id))
    events = await turns.subscribe(current_user.id, conversation_id)
    return _sse_response(conversation_id, events)


@router.post("/{conversation_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def api_stop_conversation_run(
    conversation_id: UUID,
    turns: ConversationTurnServiceDep,
    current_user: AnalysisUserDep,
) -> Response:
    """由用户显式停止 Conversation 当前的 Planner Run。"""
    await turns.stop(current_user.id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _serialize_sse_event(event: chat_schema.ChatStreamEventPayload) -> str:
    """将聊天事件序列化为 SSE 数据帧。"""
    return f"data: {event.model_dump_json()}\n\n"


async def _stream_run_events(
    conversation_id: UUID,
    events: AsyncGenerator[chat_schema.ChatStreamEventPayload],
) -> AsyncIterator[str]:
    """把后台 Run 事件投影为 SSE；连接断开只取消当前订阅。"""
    next_message_task: asyncio.Future[chat_schema.ChatStreamEventPayload] | None = None
    try:
        next_message_task = asyncio.ensure_future(anext(events))
        while True:
            done, _ = await asyncio.wait(
                {next_message_task},
                timeout=_SSE_HEARTBEAT_SECONDS,
            )
            if not done:
                yield ": keep-alive\n\n"
                continue

            try:
                event = next_message_task.result()
            except StopAsyncIteration:
                break

            yield _serialize_sse_event(event)
            next_message_task = asyncio.ensure_future(anext(events))
    except asyncio.CancelledError:
        logger.info(f"SSE 订阅断开: conversation_id={conversation_id}")
        raise
    finally:
        if next_message_task is not None and not next_message_task.done():
            next_message_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await next_message_task
        await events.aclose()


def _sse_response(
    conversation_id: UUID,
    events: AsyncGenerator[chat_schema.ChatStreamEventPayload],
) -> StreamingResponse:
    """为启动、恢复和重新订阅统一配置 SSE 心跳、清理及响应头。"""
    return StreamingResponse(
        _stream_run_events(conversation_id, events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
