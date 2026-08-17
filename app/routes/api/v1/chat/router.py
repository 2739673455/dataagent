import asyncio
import contextlib
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.clients.docker_sandbox_manager import docker_sandbox_manager
from app.core import context
from app.entities.semantic_recall import SemanticRecallRecord
from app.errors import chat_error
from app.routes.api.v1.chat import schemas as chat_schema
from app.routes.api.v1.chat.dependencies import (
    ConversationPGRepoDep,
    SemanticRecallPGRepoDep,
)
from app.services import chat_service
from app.services.semantic_recall_service import (
    SemanticRecallService,
    SemanticRecallsNotFoundError,
)

router = APIRouter(tags=["chat"])
_SSE_HEARTBEAT_SECONDS = 15


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def api_create_conversation(
    request: Request,
    body: chat_schema.CreateConversationRequest,
    conversation_repo: ConversationPGRepoDep,
) -> chat_schema.ConversationResponse:
    """创建新对话"""
    user_id = request.state.payload.sub
    conversation = await conversation_repo.create(
        user_id,
        "新对话",
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
    request: Request,
    body: chat_schema.DeleteConversationRequest,
    conversation_repo: ConversationPGRepoDep,
) -> None:
    """删除对话"""
    user_id = request.state.payload.sub

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
    request: Request,
    body: chat_schema.UpdateConversationRequest,
    conversation_repo: ConversationPGRepoDep,
) -> None:
    """修改对话信息"""
    user_id = request.state.payload.sub

    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get(user_id, body.conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError

    await conversation_repo.update(conversation, title=body.title)
    logger.info(f"conversation_id={body.conversation_id}: Update conversation")


@router.get("/ls")
async def api_get_conversations(
    request: Request,
    conversation_repo: ConversationPGRepoDep,
) -> chat_schema.ConversationListResponse:
    """获取所有对话"""
    user_id = request.state.payload.sub
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
    request: Request,
    conversation_repo: ConversationPGRepoDep,
) -> chat_schema.MessageListResponse:
    """从 LangGraph 状态获取某个对话的所有消息"""
    user_id = request.state.payload.sub
    conversation = await conversation_repo.get(user_id, conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError

    messages = await chat_service.list_messages(user_id, conversation_id)
    logger.info(f"{conversation_id=}: Get messages(count={len(messages)})")
    return chat_schema.MessageListResponse(messages=messages)


def _semantic_recall_response(
    record: SemanticRecallRecord,
) -> chat_schema.SemanticRecallResponse:
    """将领域召回记录转换为接口响应"""
    return chat_schema.SemanticRecallResponse(
        recall_id=record.recall_id,
        kind=record.kind,
        request=record.request,
        response=record.response,
        source_recall_ids=record.source_recall_ids,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("/recalls/{conversation_id}")
async def api_list_semantic_recalls(
    conversation_id: UUID,
    request: Request,
    conversation_repo: ConversationPGRepoDep,
    recall_repo: SemanticRecallPGRepoDep,
    limit: int = Query(default=100, ge=1, le=1_000),
    offset: int = Query(default=0, ge=0),
) -> chat_schema.SemanticRecallListResponse:
    """列出会话下每次查询和合并产生的语义召回记录"""
    user_id = request.state.payload.sub
    if await conversation_repo.get(user_id, conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    records = await SemanticRecallService(recall_repo).list(
        user_id,
        conversation_id,
        limit=limit,
        offset=offset,
    )
    return chat_schema.SemanticRecallListResponse(
        recalls=[_semantic_recall_response(record) for record in records]
    )


@router.get("/recalls/{conversation_id}/{recall_id}")
async def api_get_semantic_recall(
    conversation_id: UUID,
    recall_id: str,
    request: Request,
    conversation_repo: ConversationPGRepoDep,
    recall_repo: SemanticRecallPGRepoDep,
) -> chat_schema.SemanticRecallResponse:
    """读取一条语义召回记录的查询、结果和合并来源"""
    user_id = request.state.payload.sub
    if await conversation_repo.get(user_id, conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    try:
        record = await SemanticRecallService(recall_repo).get(
            user_id,
            conversation_id,
            recall_id,
        )
    except SemanticRecallsNotFoundError as exc:
        raise chat_error.SemanticRecallNotFoundError(
            extensions={"recall_ids": exc.recall_ids}
        ) from exc
    return _semantic_recall_response(record)


@router.post("/recalls/merge")
async def api_merge_semantic_recalls(
    body: chat_schema.MergeSemanticRecallsRequest,
    request: Request,
    conversation_repo: ConversationPGRepoDep,
    recall_repo: SemanticRecallPGRepoDep,
) -> chat_schema.SemanticRecallResponse:
    """去重合并多条召回记录并保存新快照"""
    user_id = request.state.payload.sub
    if await conversation_repo.get(user_id, body.conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    try:
        record = await SemanticRecallService(recall_repo).merge(
            user_id,
            body.conversation_id,
            body.recall_ids,
        )
    except SemanticRecallsNotFoundError as exc:
        raise chat_error.SemanticRecallNotFoundError(
            extensions={"recall_ids": exc.recall_ids}
        ) from exc
    return _semantic_recall_response(record)


@router.post("/recalls/delete")
async def api_delete_semantic_recalls(
    body: chat_schema.DeleteSemanticRecallsRequest,
    request: Request,
    conversation_repo: ConversationPGRepoDep,
    recall_repo: SemanticRecallPGRepoDep,
) -> chat_schema.DeleteSemanticRecallsResponse:
    """删除会话下指定的召回记录"""
    user_id = request.state.payload.sub
    if await conversation_repo.get(user_id, body.conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    deleted, missing = await SemanticRecallService(recall_repo).delete(
        user_id,
        body.conversation_id,
        body.recall_ids,
    )
    await chat_service.compact_semantic_recall_context(
        user_id,
        body.conversation_id,
    )
    return chat_schema.DeleteSemanticRecallsResponse(
        deleted_recall_ids=deleted,
        missing_recall_ids=missing,
    )


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
    user_message: chat_schema.MessageSchema,
) -> AsyncIterator[str]:
    """流式执行单轮 Agent 对话"""
    cancel = asyncio.Event()
    responses = chat_service.run_agent_turn(
        user_id,
        conversation_id,
        user_message,
        cancel,
    )
    next_message_task: asyncio.Task[chat_schema.MessageSchema] | None = None
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
    request: Request,
    body: chat_schema.ChatStreamRequest,
    conversation_repo: ConversationPGRepoDep,
) -> StreamingResponse:
    """通过 SSE 执行单轮对话并流式返回 Agent 事件"""
    user_id = request.state.payload.sub
    conversation = await conversation_repo.get(user_id, body.conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError

    if conversation.is_draft:
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
