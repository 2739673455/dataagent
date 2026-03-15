import json
import mimetypes
import re
import secrets
import shutil
from typing import Annotated
from uuid import uuid4

from app.core.agent import get_workspace_dir
from app.exceptions import chat_error
from app.mappers import message_mapper
from app.repositories import (
    context_compaction_repo,
    conversation_repo,
    message_repo,
    websocket_token_repo,
)
from app.schemas import chat_schema
from app.services import chat_service
from app.utils import context
from app.utils.db import get_app_db
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/chat", tags=["chat"])


def _build_attachment_unique_name(filename: str) -> str:
    """构造落盘文件名，尽量保留原文件名并附加唯一标识"""
    # 只保留文件名本身，避免客户端传入路径片段
    basename = filename.split("/")[-1].split("\\")[-1].strip()
    # 将特殊字符替换为下划线，保留中英文、数字和常见文件名符号
    normalized_name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", basename)
    # 去掉首尾无意义的点和下划线，并为空文件名提供兜底值
    safe_name = normalized_name.strip("._") or "upload"
    # 附加一个短随机后缀，避免同名文件互相覆盖
    unique_suffix = uuid4().hex[:4]
    # 没有扩展名时，直接在文件名末尾追加随机后缀
    if "." not in safe_name:
        return f"{safe_name}_{unique_suffix}"
    # 有扩展名时，仅在主文件名后追加随机后缀，保留原始扩展名
    stem, suffix = safe_name.rsplit(".", 1)
    return f"{stem}_{unique_suffix}.{suffix}"


def _build_attachment_path(target_dir, path: str):
    """基于工作区目录构造附件路径，并阻止路径逃逸"""
    target_path = (target_dir / path).resolve()
    if target_dir.resolve() not in target_path.parents:
        raise chat_error.ConversationNotFound
    return target_path


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
    body: chat_schema.CreateConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> chat_schema.ConversationResponse:
    """创建新对话"""
    user_id = request.state.payload.sub
    conversation = await conversation_repo.create(
        db_session, user_id, "新对话", is_draft=body.is_draft
    )
    logger.info(
        f"Create conversation: conversation_id={conversation.id}, is_draft={conversation.is_draft}"
    )
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
        # 禁用对话下所有上下文压缩记录
        await context_compaction_repo.update_yn_by_conversation_id(
            db_session, conversation_id, yn=0
        )
        # 删除对话对应工作区
        shutil.rmtree(get_workspace_dir(user_id, conversation_id), ignore_errors=True)
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

    # 从数据库加载历史消息
    message_entities = await message_repo.ls(db_session, conversation_id)
    # 获取最后一个消息的 context_seq；若没有历史消息，则首条消息从 0 开始
    cur_context_seq = message_entities[-1].context_seq if message_entities else -1
    # 将历史消息转换为 LangChain Message
    messages = [
        message_mapper.schema_to_langchain_message(
            message_mapper.entity_to_schema(i),
            user_id=user_id,
            conversation_id=conversation_id,
        )
        for i in message_entities
    ]
    logger.info(
        f"Load history messages: {conversation_id=}, message_count={len(messages)}"
    )

    # 从数据库加载最新压缩上下文
    context_compaction_entity = (
        await context_compaction_repo.get_latest_by_conversation_id(
            db_session, conversation_id
        )
    )
    if context_compaction_entity:
        # 将历史消息替换为压缩上下文
        messages[: context_compaction_entity.end_seq] = [
            {"role": "user", "content": context_compaction_entity.summary_message}
        ]
        logger.info(f"Load context_compaction: {conversation_id=}")

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

            # 将草稿对话修改为正式对话
            if conversation.is_draft == 1:
                await conversation_repo.update(db_session, conversation, is_draft=0)

            # 调用 Agent
            async for message in chat_service.stream_chat(
                db_session,
                user_id,
                conversation_id,
                messages,
                body.message,
                has_applied_summary=context_compaction_entity is not None,
            ):
                cur_context_seq += 1
                event = chat_schema.WebSocketMessageResponse(message=message)
                # 发送 WebSocket 响应
                await websocket.send_json(event.model_dump(mode="json"))

    # 客户端断开连接
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {conversation_id=}")


@router.post("/attachment/upload")
async def api_upload_attachment(
    request: Request,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    conversation_id: int = Form(...),
    file: UploadFile = File(...),
) -> chat_schema.UploadAttachmentResponse:
    """上传附件到当前会话工作区"""
    user_id = request.state.payload.sub
    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get_by_id(db_session, conversation_id)
    if (conversation is None) or (conversation.user_id != user_id):
        raise chat_error.ConversationNotFound

    # 获取原始文件名
    raw_name = file.filename or "upload"
    # 构造唯一文件名
    path = _build_attachment_unique_name(raw_name)
    # 构造文件路径
    workspace_dir = get_workspace_dir(user_id, conversation_id)
    target_path = _build_attachment_path(workspace_dir, path)
    # 按块读取并落盘，避免一次性把整个文件读入内存
    with target_path.open("wb") as target_file:
        while chunk := await file.read(1024 * 1024):
            target_file.write(chunk)

    logger.info(f"Upload attachment: {conversation_id=}, file={path}")
    return chat_schema.UploadAttachmentResponse(
        attachment=chat_schema.Attachment(raw_name=raw_name, path=path)
    )


@router.post("/attachment/delete")
async def api_delete_attachment(
    request: Request,
    body: chat_schema.DeleteAttachmentRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> None:
    """删除当前会话工作区中的附件"""
    user_id = request.state.payload.sub
    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get_by_id(db_session, body.conversation_id)
    if (conversation is None) or (conversation.user_id != user_id):
        raise chat_error.ConversationNotFound

    # 获取工作区目录并删除附件
    workspace_dir = get_workspace_dir(user_id, body.conversation_id)
    target_path = _build_attachment_path(workspace_dir, body.path)
    if target_path.exists() and target_path.is_file():
        target_path.unlink()

    logger.info(
        f"Delete attachment: conversation_id={body.conversation_id}, file={body.path}"
    )


@router.get("/attachment/get")
async def api_get_attachment(
    request: Request,
    conversation_id: int,
    path: str,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> FileResponse:
    """获取当前会话工作区中的附件文件"""
    user_id = request.state.payload.sub
    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get_by_id(db_session, conversation_id)
    if (conversation is None) or (conversation.user_id != user_id):
        raise chat_error.ConversationNotFound

    workspace_dir = get_workspace_dir(user_id, conversation_id)
    target_path = _build_attachment_path(workspace_dir, path)
    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")

    media_type, _ = mimetypes.guess_type(target_path.name)

    logger.info(f"Get attachment: {conversation_id=}, file={path}")
    return FileResponse(
        path=target_path,
        media_type=media_type or "application/octet-stream",
        filename=target_path.name,
    )
