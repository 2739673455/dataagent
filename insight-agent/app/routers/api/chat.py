import asyncio
import json
from typing import Annotated

from app.schemas.chat import (
    ConversationTitleResponse,
    GetUploadPresignedUrlRequest,
    GetUploadPresignedUrlResponse,
    MessageListResponse,
    SendMessageRequest,
    WebSocketChatRequest,
)
from app.services.chat import (
    generate_title,
    get_messages,
    stream_response,
    url_to_get_presigned_url,
)
from app.utils.log import logger
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.utils.db import get_app_db

router = APIRouter(prefix="/chat", tags=["聊天"])


@router.get("/{conversation_id}", response_model=MessageListResponse)
async def api_get_messages(
    conversation_id: int, db_session: Annotated[AsyncSession, Depends(get_app_db)]
) -> MessageListResponse:
    """获取消息记录"""
    logger.info(f"User get messages: {conversation_id=}")
    messages = await get_messages(db_session, conversation_id)
    await url_to_get_presigned_url(messages)  # 转换 cos_url 为 预签名下载url
    return MessageListResponse(messages=messages)


@router.post("/send")
async def api_send_message(
    request: Request,
    body: SendMessageRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
):
    """发送消息,获取AI流式回复"""
    logger.info(f"User send message: conversation_id={body.conversation_id}")
    return StreamingResponse(
        stream_response(
            conversation_id=body.conversation_id,
            user_id=request.state.payload.sub,
            messages=body.messages,
            base_url=body.base_url,
            model_name=body.model_name,
            api_key=body.api_key,
            params=body.params,
            db_session=db_session,
        ),
        media_type="text/plain",
    )


@router.websocket("/ws/chat")
async def api_websocket_chat(
    request: Request,
    websocket: WebSocket,
    conversation_id: int,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
):
    """WebSocket 聊天接口"""
    await websocket.accept()
    try:
        while True:
            try:
                data = await websocket.receive_json()
                logger.info(f"User websocket chat: {conversation_id=}")
                body = WebSocketChatRequest(**data)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "content": "Invalid JSON format"}
                )
                continue
            except Exception as e:
                await websocket.send_json(
                    {"type": "error", "content": f"Invalid request format: {str(e)}"}
                )
                continue

            if body.type == "chat":
                async for i in stream_response(
                    conversation_id,
                    request.state.payload.sub,
                    body.messages,
                    body.base_url,
                    body.model_name,
                    body.api_key,
                    body.params,
                    db_session,
                ):
                    await websocket.send_json(json.loads(i))
    except WebSocketDisconnect:  # 客户端断开连接
        pass
