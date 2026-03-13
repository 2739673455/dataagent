import json
import re
import secrets
from typing import Annotated
from uuid import uuid4

from app.exceptions import chat_error
from app.mappers import message_mapper
from app.repositories import conversation_repo, message_repo, websocket_token_repo
from app.schemas import chat_schema
from app.services import agent_service, chat_service
from app.utils import context
from app.utils.agent_paths import get_workspace_dir
from app.utils.db import get_app_db
from fastapi import (
    APIRouter,
    Depends,
    File,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/ls")
async def api_get_conversations(
    request: Request, db_session: Annotated[AsyncSession, Depends(get_app_db)]
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


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def api_create_conversation(
    request: Request,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> chat_schema.ConversationResponse:
    """创建新对话"""
    user_id = request.state.payload.sub
    conversation = await conversation_repo.create(db_session, user_id, "新对话")
    logger.info(f"Create conversation: conversation_id={conversation.id}")
    return chat_schema.ConversationResponse(
        conversation_id=conversation.id,
        title=conversation.title,
        update_at=conversation.update_at,
    )


@router.post("/update")
async def api_update_conversation(
    request: Request,
    body: chat_schema.UpdateConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> None:
    """修改对话信息"""
    user_id = request.state.payload.sub
    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get_by_id(db_session, body.conversation_id)
    if (conversation is None) or (conversation.user_id != user_id):
        raise chat_error.ConversationNotFound
    await conversation_repo.update(db_session, conversation, title=body.title)
    logger.info(f"Update conversation: conversation_id={body.conversation_id}")


@router.post("/delete")
async def api_delete_conversations(
    request: Request,
    body: chat_schema.DeleteConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> None:
    """删除对话(逻辑删除)"""
    user_id = request.state.payload.sub
    for conversation_id in body.conversation_ids:
        # 检查对话是否存在且属于当前用户
        conversation = await conversation_repo.get_by_id(db_session, conversation_id)
        if (conversation is None) or (conversation.user_id != user_id):
            continue
        # 禁用对话
        await conversation_repo.update(db_session, conversation, yn=0)
        # 禁用对话下所有消息
        await message_repo.update_yn_by_conversation_id(
            db_session, conversation_id, yn=0
        )
    logger.info(f"Delete conversations: conversation_ids={body.conversation_ids}")


@router.get("/ls/{conversation_id}")
async def api_get_messages(
    conversation_id: int, db_session: Annotated[AsyncSession, Depends(get_app_db)]
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
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
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

    # 检查对话是否存在且属于当前用户，如不是则关闭连接
    conversation = await conversation_repo.get_by_id(db_session, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        await websocket.send_json(
            chat_schema.WebSocketErrorResponse(
                content=chat_error.ConversationNotFound.message
            ).model_dump(mode="json")
        )
        await websocket.close(code=4404)
        logger.info(f"WebSocket disconnected: {conversation_id=}")
        return

    # 加载历史消息，并转换格式
    messages = [
        message_mapper.schema_to_langchain_message(message_mapper.entity_to_schema(i))
        for i in await message_repo.ls(db_session, conversation_id)
    ]
    logger.info(
        f"Load history messages: {conversation_id=}, message_count={len(messages)}"
    )

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
            except json.JSONDecodeError:
                await websocket.send_json(
                    chat_schema.WebSocketErrorResponse(
                        content="Invalid JSON format"
                    ).model_dump(mode="json")
                )
                continue
            except ValidationError as e:
                await websocket.send_json(
                    chat_schema.WebSocketErrorResponse(
                        content=f"Invalid request format: {str(e)}"
                    ).model_dump(mode="json")
                )
                continue

            # 调用 Agent
            async for message in chat_service.stream_chat(
                db_session,
                user_id,
                conversation_id,
                messages,
                body.message,
            ):
                event = chat_schema.WebSocketMessageResponse(message=message)
                # 发送 WebSocket 响应
                await websocket.send_json(event.model_dump(mode="json"))

    # 客户端断开连接
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {conversation_id=}")

    finally:
        # 清理 Agent
        await agent_service.cleanup_agent(user_id, conversation_id)
