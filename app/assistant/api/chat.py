"""会话管理与 Agent SSE 流式交互路由。"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.assistant import errors as chat_error
from app.assistant.api.dependencies import (
    AgentManagerDep,
    ConversationLifecycleServiceDep,
    ConversationPGRepoDep,
    ConversationRunServiceDep,
    ConversationTasksDep,
    ConversationTurnServiceDep,
    SandboxManagerDep,
)
from app.assistant.models import chat as chat_schema
from app.assistant.services import conversation as conversation_service
from app.identity.api.dependencies import CurrentUserDep
from app.shared.observability import context

router = APIRouter(tags=["chat"])
_SSE_HEARTBEAT_SECONDS = 15


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def api_create_conversation(
    body: chat_schema.CreateConversationRequest,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    tasks: ConversationTasksDep,
) -> chat_schema.ConversationResponse:
    """创建新对话。"""
    user_id = current_user.id
    initial_message = (body.initial_message or "").strip()
    async with conversation_repo.session.begin():
        conversation = await conversation_repo.create(
            user_id,
            initial_message[:64] or "新对话",
            is_draft=body.is_draft,
        )
    if initial_message and not body.is_draft:
        tasks.generate_title(
            user_id,
            conversation.id,
            initial_message,
        )

    logger.info(
        f"创建对话: conversation_id={conversation.id}, is_draft={conversation.is_draft}"
    )
    return chat_schema.ConversationResponse(
        conversation_id=conversation.id,
        title=conversation.title,
        update_at=conversation.update_at,
        running=False,
    )


@router.post("/delete")
async def api_delete_conversations(
    body: chat_schema.DeleteConversationRequest,
    current_user: CurrentUserDep,
    lifecycle: ConversationLifecycleServiceDep,
    tasks: ConversationTasksDep,
) -> None:
    """删除对话。"""
    user_id = current_user.id

    for conversation_id in body.conversation_ids:
        if not await lifecycle.request_conversation_deletion(
            user_id,
            conversation_id,
        ):
            raise chat_error.ConversationNotFoundError
        try:
            tasks.delete_conversation(user_id, conversation_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                f"提交会话删除任务失败，等待定时补偿: conversation_id={conversation_id}"
            )

    logger.info(f"删除对话: conversation_ids={body.conversation_ids}")


@router.get("/ls")
async def api_get_conversations(
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    runs: ConversationRunServiceDep,
) -> chat_schema.ConversationListResponse:
    """获取所有对话。"""
    user_id = current_user.id
    conversations = await conversation_repo.list_by_user(user_id)
    running_conversation_ids = runs.running_conversation_ids(user_id)
    logger.info(f"获取对话列表: conversation_ids={[item.id for item in conversations]}")
    return chat_schema.ConversationListResponse(
        conversations=[
            chat_schema.ConversationResponse(
                conversation_id=item.id,
                title=item.title,
                update_at=item.update_at,
                running=item.id in running_conversation_ids,
            )
            for item in conversations
        ]
    )


@router.get("/ls/{conversation_id}")
async def api_get_messages(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    agents: AgentManagerDep,
    sandbox: SandboxManagerDep,
) -> chat_schema.MessageListResponse:
    """从 LangGraph 状态获取某个对话的所有消息。"""
    user_id = current_user.id
    conversation = await conversation_repo.get(user_id, conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError
    messages = await conversation_service.list_messages(
        agents,
        sandbox,
        user_id,
        conversation_id,
    )
    logger.info(
        f"获取消息列表: conversation_id={conversation_id}, count={len(messages)}"
    )
    return chat_schema.MessageListResponse(messages=messages)


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
    current_user: CurrentUserDep,
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
    current_user: CurrentUserDep,
) -> StreamingResponse:
    """从中断的 Planner Checkpoint 继续当前用户回合。"""
    user_id = current_user.id
    context.user_id_ctx.set(str(user_id))
    events = await turns.resume(user_id, conversation_id)
    return _sse_response(conversation_id, events)


@router.get("/{conversation_id}/run")
async def api_get_conversation_run_status(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    runs: ConversationRunServiceDep,
) -> chat_schema.ConversationRunStatusResponse:
    """查询 Conversation 是否有正在后台执行的 Planner Run。"""
    user_id = current_user.id
    if await conversation_repo.get(user_id, conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    return chat_schema.ConversationRunStatusResponse(
        running=runs.is_running(user_id, conversation_id)
    )


@router.get("/{conversation_id}/events", response_class=StreamingResponse)
async def api_subscribe_conversation_run(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    runs: ConversationRunServiceDep,
) -> StreamingResponse:
    """订阅已经启动的后台 Planner Run。"""
    user_id = current_user.id
    if await conversation_repo.get(user_id, conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    context.user_id_ctx.set(str(user_id))
    events = runs.subscribe(user_id, conversation_id)
    return _sse_response(conversation_id, events)


@router.post("/{conversation_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def api_stop_conversation_run(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    runs: ConversationRunServiceDep,
) -> Response:
    """由用户显式停止 Conversation 当前的 Planner Run。"""
    user_id = current_user.id
    if await conversation_repo.get(user_id, conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    await runs.stop(user_id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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

            yield f"data: {event.model_dump_json()}\n\n"
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
