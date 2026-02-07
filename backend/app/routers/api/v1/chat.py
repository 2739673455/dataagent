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
from app.schemas.user import AccessTokenPayload
from app.services.chat import (
    generate_title,
    get_messages,
    image_url_to_get_presigned_url,
    stream_response,
)
from app.services.database import get_app_db
from app.utils.cos import generate_cos_key, get_upload_presigned_url
from app.utils.log import logger
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/chat", tags=["聊天"])


@router.get("/{conversation_id}", response_model=MessageListResponse)
async def api_get_messages(
    conversation_id: int,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> MessageListResponse:
    """获取消息记录"""
    logger.info(f"User get messages: {conversation_id=}")
    messages = await get_messages(db_session, conversation_id)
    # 转换cos_url为预签名下载url
    await image_url_to_get_presigned_url(messages)
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
    request: GetUploadPresignedUrlRequest,
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> GetUploadPresignedUrlResponse:
    """获取带预签名的上传url"""
    logger.info(
        f"User get upload presigned url: conversation_id={request.conversation_id}"
    )
    cos_keys = [
        generate_image_cos_key(payload.sub, request.conversation_id, suffix)
        for suffix in request.suffixes
    ]  # 生成cos_key
    upload_presigned_urls = await asyncio.gather(
        *[get_upload_presigned_url(key) for key in cos_keys]
    )  # 获取预签名上传url
    return GetUploadPresignedUrlResponse(urls=upload_presigned_urls)


@router.post("/generate_title", response_model=ConversationTitleResponse)
async def api_generate_conversation_title(
    request: SendMessageRequest,
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> ConversationTitleResponse:
    """生成对话标题"""
    logger.info(f"User generate conversation title: {request.conversation_id}")
    # 转换预签名上传url为预签名下载url
    await image_url_to_get_presigned_url(request.messages)
    # 生成标题
    title = await generate_title(
        request.messages[0].content,
        request.base_url,
        request.model_name,
        request.api_key,
    )
    return ConversationTitleResponse(title=title)


@router.post("/send")
async def api_send_message(
    request: SendMessageRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
):
    """发送消息,获取AI流式回复"""
    logger.info(f"User send message: conversation_id={request.conversation_id}")
    return StreamingResponse(
        stream_response(
            conversation_id=request.conversation_id,
            user_id=payload.sub,
            messages=request.messages,
            base_url=request.base_url,
            model_name=request.model_name,
            api_key=request.api_key,
            params=request.params,
            db_session=db_session,
        ),
        media_type="text/plain",
    )


@router.websocket("/ws/chat")
async def api_websocket_chat(
    websocket: WebSocket,
    conversation_id: int,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
):
    """WebSocket 聊天接口"""
    await websocket.accept()
    try:
        while True:
            try:
                data = await websocket.receive_json()
                logger.info(f"User websocket chat: {conversation_id=}")
                request = WebSocketChatRequest(**data)
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

            if request.type == "chat":
                async for i in stream_response(
                    conversation_id,
                    payload.sub,
                    request.messages,
                    request.base_url,
                    request.model_name,
                    request.api_key,
                    request.params,
                    db_session,
                ):
                    await websocket.send_json(json.loads(i))
    except WebSocketDisconnect:  # 客户端断开连接
        pass
