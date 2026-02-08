import asyncio
import json
from typing import Annotated

from app.schemas.chat import (
    ConversationTitleResponse,
    GetUploadPresignedUrlRequest,
    GetUploadPresignedUrlResponse,
    MessageItem,
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
from app.utils.cos import generate_cos_key, get_upload_presigned_url
from app.utils.database import get_app_db
from app.utils.log import logger
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/chat", tags=["聊天"])


@router.get("/{conversation_id}", response_model=MessageListResponse)
async def api_get_messages(
    conversation_id: int, db_session: Annotated[AsyncSession, Depends(get_app_db)]
) -> MessageListResponse:
    """获取消息记录"""
    logger.info(f"User get messages: {conversation_id=}")
    messages = await get_messages(db_session, conversation_id)
    # 转换cos_url为预签名下载url
    await url_to_get_presigned_url(messages)
    return MessageListResponse(
        messages=[
            MessageItem(
                message_id=message.id,
                role=message.role,
                content=message.content,
                timestamp=message.timestamp,
            )
            for message in messages
        ]
    )


@router.post("/get_upload_presigned_url", response_model=GetUploadPresignedUrlResponse)
async def api_get_upload_presigned_url(
    request: Request, body: GetUploadPresignedUrlRequest
) -> GetUploadPresignedUrlResponse:
    """获取带预签名的上传url"""
    logger.info(
        f"User get upload presigned url: conversation_id={body.conversation_id}"
    )
    cos_keys = [
        generate_cos_key(request.state.payload.sub, body.conversation_id, suffix)
        for suffix in body.suffixes
    ]  # 生成cos_key
    upload_presigned_urls = await asyncio.gather(
        *[get_upload_presigned_url(key) for key in cos_keys]
    )  # 获取预签名上传url
    return GetUploadPresignedUrlResponse(urls=upload_presigned_urls)


@router.post("/generate_title", response_model=ConversationTitleResponse)
async def api_generate_conversation_title(
    body: SendMessageRequest,
) -> ConversationTitleResponse:
    """生成对话标题"""
    logger.info(
        f"User generate conversation title: conversation_id={body.conversation_id}"
    )
    # 转换预签名上传url为预签名下载url
    await url_to_get_presigned_url(body.messages)
    # 生成标题
    title = await generate_title(
        body.messages[0].content,
        body.base_url,
        body.model_name,
        body.api_key,
    )
    return ConversationTitleResponse(title=title)


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
