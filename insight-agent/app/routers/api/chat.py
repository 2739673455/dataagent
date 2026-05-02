import asyncio
import json
import secrets
import shutil
from typing import Annotated

from app.agent.agent import get_workspace_dir
from app.core import context
from app.core.database import get_db, get_db_session
from app.errors import chat_error
from app.mappers import message_mapper
from app.repositories import (
    context_compaction_repo,
    conversation_repo,
    message_repo,
    websocket_token_repo,
)
from app.schemas import chat_schema
from app.services import chat_service
from fastapi import (
    APIRouter,
    Depends,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["chat"])


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def api_create_conversation(
    request: Request,
    body: chat_schema.CreateConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_db)],
) -> chat_schema.ConversationResponse:
    """创建新对话"""
    user_id = request.state.payload.sub

    conversation = await conversation_repo.create(
        db_session,
        user_id,
        "新对话",
        is_draft=body.is_draft,
    )

    logger.info(
        f"Create conversation: conversation_id={conversation.id}, is_draft={conversation.is_draft}"
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
    db_session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """删除对话(逻辑删除)"""
    user_id = request.state.payload.sub

    for conversation_id in body.conversation_ids:
        # 检查对话是否存在且属于当前用户
        conversation = await conversation_repo.get_by_id(db_session, conversation_id)
        if (conversation is None) or (conversation.user_id != user_id):
            raise chat_error.ConversationNotFound

        # 禁用对话
        await conversation_repo.update(db_session, conversation, yn=0)
        # 禁用对话下所有消息
        await message_repo.update_yn_by_conversation_id(
            db_session, conversation_id, yn=0
        )
        # 禁用对话下所有上下文压缩记录
        await context_compaction_repo.update_yn_by_conversation_id(
            db_session, conversation_id, yn=0
        )

        # 删除对话对应工作区
        await asyncio.to_thread(
            shutil.rmtree,
            get_workspace_dir(user_id, conversation_id),
            ignore_errors=True,
        )

    logger.info(f"Delete conversations: conversation_ids={body.conversation_ids}")


@router.post("/update")
async def api_update_conversation(
    request: Request,
    body: chat_schema.UpdateConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """修改对话信息"""
    user_id = request.state.payload.sub

    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get_by_id(db_session, body.conversation_id)
    if (conversation is None) or (conversation.user_id != user_id):
        raise chat_error.ConversationNotFound

    await conversation_repo.update(db_session, conversation, title=body.title)

    logger.info(f"Update conversation: conversation_id={body.conversation_id}")


@router.get("/ls")
async def api_get_conversations(
    request: Request, db_session: Annotated[AsyncSession, Depends(get_db)]
) -> chat_schema.ConversationListResponse:
    """获取所有对话"""
    user_id = request.state.payload.sub

    conversations = await conversation_repo.ls(db_session, user_id)

    logger.info(f"Get conversations: conversation_ids={[i.id for i in conversations]}")

    return chat_schema.ConversationListResponse(
        conversations=[
            chat_schema.ConversationResponse(
                conversation_id=i.id,
                title=i.title,
                update_at=i.update_at,
            )
            for i in conversations
        ]
    )


@router.get("/ls/{conversation_id}")
async def api_get_messages(
    conversation_id: int, db_session: Annotated[AsyncSession, Depends(get_db)]
) -> chat_schema.MessageListResponse:
    """获取某个对话所有消息"""
    messages = await message_repo.ls(db_session, conversation_id)
    logger.info(f"Get messages: {conversation_id=}, message_count={len(messages)}")
    return chat_schema.MessageListResponse(
        messages=[message_mapper.entity_to_schema(message) for message in messages]
    )


@router.post("/ws-token")
async def api_create_websocket_token(
    request: Request,
) -> chat_schema.WebSocketTokenResponse:
    """创建 WebSocket 临时令牌"""
    # 临时令牌过期时间
    WS_TOKEN_EXPIRE_SECONDS = 30

    # 获取用户ID
    user_id = request.state.payload.sub

    # 创建 WebSocket 临时令牌
    websocket_token = secrets.token_urlsafe(32)
    # 存储 WebSocket 临时令牌
    await websocket_token_repo.create(
        token=websocket_token,
        user_id=user_id,
        expire_seconds=WS_TOKEN_EXPIRE_SECONDS,
    )

    logger.info("Create websocket token")

    return chat_schema.WebSocketTokenResponse(
        websocket_token=websocket_token,
        expires_in=WS_TOKEN_EXPIRE_SECONDS,
    )


@router.websocket("/ws/chat")
async def api_websocket_chat(
    websocket: WebSocket,
    conversation_id: int,
):
    """WebSocket 聊天接口"""
    # 检查 WebSocket 临时令牌(从请求参数中获取)
    websocket_token = websocket.query_params.get("websocket_token")
    if not websocket_token:
        await websocket.close(code=4401)
        return
    token_data = await websocket_token_repo.consume(websocket_token)
    if token_data is None:
        await websocket.close(code=4401)
        return
    user_id = token_data.user_id

    # 将用户ID添加到上下文变量
    context.user_id_ctx.set(str(user_id))

    # 建立 WebSocket 连接
    await websocket.accept()
    logger.info(f"WebSocket connected: {conversation_id=}")

    # 加载会话初始上下文
    ctx = await chat_service.load_conversation_context(conversation_id, user_id)
    if ctx is None:
        await websocket.send_json(
            chat_schema.WebSocketErrorResponse(
                content=chat_error.ConversationNotFound.title
            ).model_dump(mode="json")
        )
        await websocket.close(code=4404)
        logger.info(f"WebSocket disconnected: {conversation_id=}")
        return
    messages, cur_context_seq, is_draft, has_applied_summary = ctx

    try:
        while True:
            # 接收并解析 WebSocket 请求
            try:
                body = chat_schema.WebSocketChatRequest(
                    **await websocket.receive_json()
                )
                # 检查是否为用户消息
                if body.message.role != "user":
                    await websocket.send_json(
                        chat_schema.WebSocketErrorResponse(
                            content="Invalid request format: message.role must be 'user'"
                        ).model_dump(mode="json")
                    )
                    continue
                # 为用户消息添加 context_seq
                cur_context_seq += 1
                body.message.context_seq = cur_context_seq
            except (json.JSONDecodeError, ValidationError) as e:
                await websocket.send_json(
                    chat_schema.WebSocketErrorResponse(
                        content=f"Invalid request: {str(e)}"
                    ).model_dump(mode="json")
                )
                continue

            # 每轮对话使用独立的 DB 会话，避免长连接占用连接池
            async with get_db_session() as db_session:
                # 将草稿对话修改为正式对话
                if is_draft:
                    conversation = await conversation_repo.get_by_id(
                        db_session, conversation_id
                    )
                    if conversation:
                        await conversation_repo.update(
                            db_session, conversation, is_draft=0
                        )
                        is_draft = False

                # 调用 Agent 流式生成回复
                async for message in chat_service.stream_chat(
                    db_session,
                    user_id,
                    conversation_id,
                    messages,
                    body.message,
                    has_applied_summary=has_applied_summary,
                ):
                    cur_context_seq += 1
                    event = chat_schema.WebSocketMessageResponse(message=message)
                    # 发送 WebSocket 响应
                    await websocket.send_json(event.model_dump(mode="json"))

                # 本轮对话结束后，后续轮次的 context_seq 从上一个值递增
                has_applied_summary = True

    # 客户端断开连接
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {conversation_id=}")
