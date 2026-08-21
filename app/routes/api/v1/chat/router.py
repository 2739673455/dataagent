"""对话管理、语义召回与 Agent SSE 流式交互路由"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.clients.docker_sandbox_manager import docker_sandbox_manager
from app.core import context
from app.errors import chat_error
from app.routes.api.v1.auth.dependencies import AnalysisUserDep, CurrentUserDep
from app.routes.api.v1.chat import schemas as chat_schema
from app.routes.api.v1.chat.dependencies import (
    ConversationPGRepoDep,
)
from app.services import chat_service
from app.services.conversation_title_service import (
    conversation_title_service,
    initial_conversation_title,
)

router = APIRouter(tags=["chat"])
_SSE_HEARTBEAT_SECONDS = 15


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def api_create_conversation(
    body: chat_schema.CreateConversationRequest,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
) -> chat_schema.ConversationResponse:
    """创建新对话"""
    user_id = current_user.id
    conversation = await conversation_repo.create(
        user_id,
        initial_conversation_title(body.initial_message),
        is_draft=body.is_draft,
    )

    logger.info(
        f"conversation_id={conversation.id}: Create conversation(is_draft={conversation.is_draft})"
    )
    return chat_schema.ConversationResponse(
        conversation_id=conversation.id,
        title=conversation.title,
        update_at=conversation.update_at,
    )


@router.post("/delete")
async def api_delete_conversations(
    body: chat_schema.DeleteConversationRequest,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
) -> None:
    """删除对话"""
    user_id = current_user.id

    for conversation_id in body.conversation_ids:
        # 检查对话是否存在且属于当前用户
        conversation = await conversation_repo.get(user_id, conversation_id)
        if conversation is None:
            raise chat_error.ConversationNotFoundError

        # 删除 LangGraph 线程状态
        await chat_service.delete_conversation_data(user_id, conversation_id)
        # 删除用户沙盒中的会话目录
        await docker_sandbox_manager.delete_conversation(user_id, conversation_id)
        # 删除会话目录信息
        await conversation_repo.delete(user_id, conversation_id)

    logger.info(f"Delete conversations: conversation_ids={body.conversation_ids}")


@router.post("/update")
async def api_update_conversation(
    body: chat_schema.UpdateConversationRequest,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
) -> None:
    """修改对话信息"""
    user_id = current_user.id

    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get(user_id, body.conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError

    await conversation_repo.update(
        conversation,
        title=body.title,
        title_pending=False,
    )
    logger.info(f"conversation_id={body.conversation_id}: Update conversation")


@router.get("/ls")
async def api_get_conversations(
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
) -> chat_schema.ConversationListResponse:
    """获取所有对话"""
    user_id = current_user.id
    conversations = await conversation_repo.list_by_user(user_id)
    logger.info(
        f"Get conversations: conversation_ids={[item.id for item in conversations]}"
    )
    return chat_schema.ConversationListResponse(
        conversations=[
            chat_schema.ConversationResponse(
                conversation_id=item.id,
                title=item.title,
                update_at=item.update_at,
            )
            for item in conversations
        ]
    )


@router.get("/ls/{conversation_id}")
async def api_get_messages(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
) -> chat_schema.MessageListResponse:
    """从 LangGraph 状态获取某个对话的所有消息"""
    user_id = current_user.id
    conversation = await conversation_repo.get(user_id, conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError

    messages = await chat_service.list_messages(user_id, conversation_id)
    logger.info(f"{conversation_id=}: Get messages(count={len(messages)})")
    return chat_schema.MessageListResponse(messages=messages)


ChatStreamEvent = (
    chat_schema.ChatStreamMessageEvent
    | chat_schema.ChatStreamErrorEvent
    | chat_schema.ChatStreamDoneEvent
)


def _serialize_sse_event(event: ChatStreamEvent) -> str:
    """将聊天事件序列化为 SSE 数据帧"""
    return f"data: {event.model_dump_json()}\n\n"


async def _stream_agent_response(
    user_id: int,
    conversation_id: UUID,
    user_message: chat_schema.UserMessageRequest,
) -> AsyncIterator[str]:
    """流式执行单轮 Agent 对话"""
    cancel = asyncio.Event()
    responses = chat_service.run_agent_turn(
        user_id,
        conversation_id,
        user_message,
        cancel,
    )
    next_message_task: asyncio.Task[chat_schema.MessageResponse] | None = None
    try:
        next_message_task = asyncio.create_task(anext(responses))
        while True:
            done, _ = await asyncio.wait(
                {next_message_task},
                timeout=_SSE_HEARTBEAT_SECONDS,
            )
            if not done:
                yield ": keep-alive\n\n"
                continue

            try:
                message = next_message_task.result()
            except StopAsyncIteration:
                break

            yield _serialize_sse_event(
                chat_schema.ChatStreamMessageEvent(message=message)
            )
            next_message_task = asyncio.create_task(anext(responses))
    except asyncio.CancelledError:
        logger.info(f"{conversation_id=}: SSE stream disconnected")
        raise
    except Exception:  # noqa: BLE001
        logger.exception(f"{conversation_id=}: agent failed")
        yield _serialize_sse_event(
            chat_schema.ChatStreamErrorEvent(content="模型调用失败，请稍后重试。")
        )
    else:
        yield _serialize_sse_event(chat_schema.ChatStreamDoneEvent())
    finally:
        cancel.set()
        if next_message_task is not None and not next_message_task.done():
            next_message_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await next_message_task
        await responses.aclose()


@router.post("/stream", response_class=StreamingResponse)
async def api_stream_chat(
    body: chat_schema.ChatStreamRequest,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
) -> StreamingResponse:
    """通过 SSE 执行单轮对话并流式返回 Agent 事件"""
    user_id = current_user.id
    conversation = await conversation_repo.get(user_id, body.conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError

    user_text = "\n".join(
        part.text
        for part in body.message.parts
        if isinstance(part, chat_schema.TextContent)
    ).strip()
    if conversation.title_pending and user_text:
        conversation = await conversation_repo.update(
            conversation,
            title=initial_conversation_title(user_text),
            title_pending=False,
            is_draft=False,
        )
        conversation_title_service.schedule(
            conversation_repo,
            user_id,
            conversation.id,
            conversation.title,
            user_text,
        )
    elif conversation.is_draft:
        await conversation_repo.update(conversation, is_draft=False)
    else:
        await conversation_repo.update(conversation)

    context.user_id_ctx.set(str(user_id))
    return StreamingResponse(
        _stream_agent_response(
            user_id,
            body.conversation_id,
            body.message,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
