import json
from typing import Annotated

from app.exceptions import chat as chat_error
from app.exceptions.base import AppError
from app.middlewares import auth as auth_middleware
from app.repositories import conversation as conversation_repo
from app.repositories import message as message_repo
from app.schemas import chat as chat_schema
from app.services import agent as agent_service
from app.utils.db import get_app_db
from app.utils.log import logger
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/chat")


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
    body: chat_schema.UpdateConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> None:
    """修改对话信息"""
    conversation = await conversation_repo.get_by_id(db_session, body.conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFound
    await conversation_repo.update(db_session, conversation, title=body.title)
    logger.info(f"Update conversation: conversation_id={body.conversation_id}")


@router.post("/delete")
async def api_delete_conversations(
    body: chat_schema.DeleteConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> None:
    """删除对话(逻辑删除)"""
    for conversation_id in body.conversation_ids:
        conversation = await conversation_repo.get_by_id(db_session, conversation_id)
        if conversation is None:
            raise chat_error.ConversationNotFound
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
    logger.info(f"Get messages: {conversation_id=}")
    return chat_schema.MessageListResponse(
        messages=[chat_schema.MessageItem.from_entity(message) for message in messages]
    )


async def _receive_chat_request(
    websocket: WebSocket,
) -> chat_schema.WebSocketChatRequest | None:
    """接收并解析 WebSocket 请求"""
    try:
        return chat_schema.WebSocketChatRequest(**await websocket.receive_json())
    except json.JSONDecodeError:
        await websocket.send_json(
            chat_schema.WebSocketErrorResponse(
                content="Invalid JSON format"
            ).model_dump(mode="json")
        )
    except ValidationError as e:
        await websocket.send_json(
            chat_schema.WebSocketErrorResponse(
                content=f"Invalid request format: {str(e)}"
            ).model_dump(mode="json")
        )
    return None


@router.websocket("/ws/chat")
async def api_websocket_chat(
    websocket: WebSocket,
    conversation_id: int,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
):
    """WebSocket 聊天接口"""
    # 认证
    try:
        payload = await auth_middleware.authenticate_authorization(
            websocket.headers.get("Authorization")
        )
    except AppError:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    try:
        while True:
            # 接收并解析 WebSocket 请求
            body = await _receive_chat_request(websocket)
            if body is None:
                continue

            # 处理请求
            async for event in agent_service.astream(
                payload.sub,
                conversation_id,
                body.message,
            ):
                # 发送响应
                await websocket.send_json(event.model_dump(mode="json"))

    except WebSocketDisconnect:  # 客户端断开连接
        pass
