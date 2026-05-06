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

    logger.info(f"conversation_id={body.conversation_id}: Update conversation")


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
    logger.info(f"{conversation_id=}: Get messages(count={len(messages)})")
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


async def _validate_and_accept(
    websocket: WebSocket, conversation_id: int
) -> int | None:
    """校验 WebSocket 令牌并接受连接，返回 user_id；失败时关闭连接返回 None"""
    # 从请求参数中获取 WebSocket 临时令牌
    websocket_token = websocket.query_params.get("websocket_token")
    if not websocket_token:
        # 缺少 WebSocket 临时令牌则拒绝连接
        await websocket.close(code=4401)
        return None

    # 使用 WebSocket 临时令牌获取用户信息
    token_data = await websocket_token_repo.consume(websocket_token)
    if token_data is None:
        # 无法获取用户信息则拒绝连接
        await websocket.close(code=4401)
        return None

    # 获取用户 ID 并放入上下文变量
    user_id = token_data.user_id
    context.user_id_ctx.set(str(user_id))

    # 接收 WebSocket 连接
    await websocket.accept()
    logger.info(f"{conversation_id=}: WebSocket connected")
    return user_id


async def _receive_user_message(
    websocket: WebSocket,
) -> chat_schema.MessageSchema | None:
    """接收并校验用户消息；cancel 或格式错误时返回 None"""
    try:
        raw = await websocket.receive_json()
    except RuntimeError:
        return None

    # 接收到取消请求
    if isinstance(raw, dict) and raw.get("type") == "cancel":
        return None

    # 校验消息格式
    try:
        body = chat_schema.WebSocketChatRequest(**raw)
    except (json.JSONDecodeError, ValidationError) as e:
        # 格式错误则发送错误响应
        await websocket.send_json(
            chat_schema.WebSocketErrorResponse(
                content=f"Invalid request: {str(e)}"
            ).model_dump(mode="json")
        )
        return None

    # 校验消息角色
    if body.message.role != "user":
        # 非用户消息则发送错误响应
        await websocket.send_json(
            chat_schema.WebSocketErrorResponse(
                content="Invalid request format: message.role must be 'user'"
            ).model_dump(mode="json")
        )
        return None

    return body.message


async def _ensure_not_draft(db_session: AsyncSession, conversation_id: int) -> None:
    """草稿对话转正式对话"""
    conversation = await conversation_repo.get_by_id(db_session, conversation_id)
    if conversation:
        await conversation_repo.update(db_session, conversation, is_draft=0)


async def _run_turn_and_send(
    websocket: WebSocket,
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    user_message: chat_schema.MessageSchema,
) -> tuple[bool, int]:
    """
    执行一轮 Agent 并将响应通过 WebSocket 发送；返回 (disconnected, cur_context_seq)

    _run() 与 _wait_cancel() 并行运行：
    _run() 调用 Agent 并逐条推送响应；
    _wait_cancel() 监听客户端 cancel 消息。任一检测到断开即设置 cancel 通知 Agent 中断。
    """
    cancel = asyncio.Event()  # 取消标志
    disconnected = False  # 断开标志
    cur_context_seq = user_message.context_seq or 0  # 追踪最新 context_seq

    async def _run():
        # Agent 流式生成 → 逐条推送给客户端
        nonlocal disconnected, cur_context_seq
        async for msg in chat_service.run_agent_turn(
            db_session,
            user_id,
            conversation_id,
            messages,
            user_message,
            cancel=cancel,
        ):
            cur_context_seq = msg.context_seq or cur_context_seq
            try:
                # 向客户端发送消息
                await websocket.send_json(
                    chat_schema.WebSocketMessageResponse(message=msg).model_dump(
                        mode="json"
                    )
                )
            except WebSocketDisconnect:
                # WebSocket 断开，设置断开标志和取消标志，跳出循环
                disconnected = True
                cancel.set()
                break

    async def _wait_cancel():
        # 监听客户端 cancel 消息，收到则通知 Agent 中断
        nonlocal disconnected
        try:
            while True:
                raw = await websocket.receive_json()
                if isinstance(raw, dict) and raw.get("type") == "cancel":
                    cancel.set()  # 设置取消标志
                    logger.info(f"{conversation_id=}: Received cancel signal")
                    return
        except (WebSocketDisconnect, RuntimeError):
            disconnected = True

    # 并行执行 Agent 生成与 cancel 监听，任一完成即进入收尾
    run_task = asyncio.create_task(_run())
    cancel_task = asyncio.create_task(_wait_cancel())

    try:
        await run_task
    except Exception as exc:
        # Agent 内部未捕获的异常（非 WebSocket 断开）
        logger.exception(f"{conversation_id=}: agent failed: {exc!r}")
        if not disconnected:
            try:
                await websocket.send_json(
                    chat_schema.WebSocketErrorResponse(
                        content="模型调用失败，请稍后重试。"
                    ).model_dump(mode="json")
                )
            except WebSocketDisconnect:
                disconnected = True

    # 收尾：run_task执行结束后，取消 cancel 监听，等待其退出
    cancel_task.cancel()
    try:
        await cancel_task
    except asyncio.CancelledError:
        pass

    return disconnected, cur_context_seq


@router.websocket("/ws/chat")
async def api_websocket_chat(
    websocket: WebSocket,
    conversation_id: int,
):
    """WebSocket 聊天接口"""

    # ========== Phase 1: 校验令牌、建立连接 ==========
    user_id = await _validate_and_accept(websocket, conversation_id)
    if user_id is None:
        return

    # ========== Phase 2: 加载对话上下文 ==========
    ctx = await chat_service.load_conversation_context(conversation_id, user_id)
    if ctx is None:
        # 对话不存在则发送错误响应，并关闭连接
        await websocket.send_json(
            chat_schema.WebSocketErrorResponse(
                content=chat_error.ConversationNotFound.title
            ).model_dump(mode="json")
        )
        await websocket.close(code=4404)
        logger.info(f"{conversation_id=}: WebSocket disconnected")
        return
    messages, cur_context_seq, is_draft = ctx

    # ========== Phase 3: 消息循环 ==========
    try:
        while True:
            # 接收用户消息
            user_message = await _receive_user_message(websocket)
            if user_message is None:
                continue  # 跳过无效消息，继续等待下一条

            # 上下文序号+1，并更新用户消息序号
            cur_context_seq += 1
            user_message.context_seq = cur_context_seq

            async with get_db_session() as db_session:
                if is_draft:
                    # 草稿对话转正式对话
                    await _ensure_not_draft(db_session, conversation_id)
                    is_draft = False
                disconnected, cur_context_seq = await _run_turn_and_send(
                    websocket,
                    db_session,
                    user_id,
                    conversation_id,
                    messages,
                    user_message,
                )
                if disconnected:
                    break

    except (WebSocketDisconnect, RuntimeError):
        logger.info(f"{conversation_id=}: WebSocket disconnected")
