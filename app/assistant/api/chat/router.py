"""对话管理、语义召回与 Agent SSE 流式交互路由。"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.assistant import errors as chat_error
from app.assistant.api.chat import schemas as chat_schema
from app.assistant.api.chat.dependencies import (
    ConversationPGRepoDep,
)
from app.assistant.api.dependencies import (
    AgentManagerDep,
    ConversationLifecycleServiceDep,
    ConversationRunServiceDep,
    SandboxManagerDep,
)
from app.assistant.services import chat as chat_service
from app.assistant.services.conversation_run import (
    ConversationRunAlreadyActiveError as ActiveRunConflict,
)
from app.assistant.services.conversation_title import (
    initial_conversation_title,
)
from app.assistant.tasks import (
    enqueue_conversation_deletion,
    enqueue_conversation_title,
)
from app.identity.api.auth.dependencies import AnalysisUserDep, CurrentUserDep
from app.shared.contracts.analysis import AgentType
from app.shared.observability import context

router = APIRouter(tags=["chat"])
_SSE_HEARTBEAT_SECONDS = 15


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def api_create_conversation(
    body: chat_schema.CreateConversationRequest,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
) -> chat_schema.ConversationResponse:
    """创建新对话。"""
    user_id = current_user.id
    async with conversation_repo.session.begin():
        conversation = await conversation_repo.create(
            user_id,
            initial_conversation_title(body.initial_message),
            is_draft=body.is_draft,
        )

    logger.info(
        f"创建对话: conversation_id={conversation.id}, is_draft={conversation.is_draft}"
    )
    return chat_schema.ConversationResponse(
        conversation_id=conversation.id,
        title=conversation.title,
        update_at=conversation.update_at,
        running=False,
    )


@router.post("/delete")
async def api_delete_conversations(
    body: chat_schema.DeleteConversationRequest,
    current_user: CurrentUserDep,
    lifecycle: ConversationLifecycleServiceDep,
) -> None:
    """删除对话。"""
    user_id = current_user.id

    for conversation_id in body.conversation_ids:
        if not await lifecycle.request_conversation_deletion(
            user_id,
            conversation_id,
        ):
            raise chat_error.ConversationNotFoundError
        try:
            enqueue_conversation_deletion(user_id, conversation_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                f"提交会话删除任务失败，等待定时补偿: conversation_id={conversation_id}"
            )

    logger.info(f"删除对话: conversation_ids={body.conversation_ids}")


@router.delete(
    "/draft/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def api_delete_draft_conversation(
    conversation_id: UUID,
    current_user: CurrentUserDep,
    lifecycle: ConversationLifecycleServiceDep,
) -> Response:
    """幂等删除当前用户主动放弃的草稿会话。"""
    requested = await lifecycle.request_conversation_deletion(
        current_user.id,
        conversation_id,
        draft_only=True,
    )
    if requested:
        try:
            enqueue_conversation_deletion(current_user.id, conversation_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                f"提交草稿删除任务失败，等待定时补偿: conversation_id={conversation_id}"
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/update")
async def api_update_conversation(
    body: chat_schema.UpdateConversationRequest,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
) -> None:
    """修改对话信息。"""
    user_id = current_user.id

    async with conversation_repo.session.begin():
        # 检查对话是否存在且属于当前用户。
        conversation = await conversation_repo.get(user_id, body.conversation_id)
        if conversation is None:
            raise chat_error.ConversationNotFoundError

        await conversation_repo.update(
            conversation,
            title=body.title,
            title_pending=False,
        )
    logger.info(f"更新对话: conversation_id={body.conversation_id}")


@router.get("/ls")
async def api_get_conversations(
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    runs: ConversationRunServiceDep,
) -> chat_schema.ConversationListResponse:
    """获取所有对话。"""
    user_id = current_user.id
    conversations = await conversation_repo.list_by_user(user_id)
    running_conversation_ids = await runs.running_conversation_ids(user_id)
    logger.info(f"获取对话列表: conversation_ids={[item.id for item in conversations]}")
    return chat_schema.ConversationListResponse(
        conversations=[
            chat_schema.ConversationResponse(
                conversation_id=item.id,
                title=item.title,
                update_at=item.update_at,
                running=item.id in running_conversation_ids,
            )
            for item in conversations
        ]
    )


@router.get("/ls/{conversation_id}")
async def api_get_messages(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    agents: AgentManagerDep,
    sandbox: SandboxManagerDep,
) -> chat_schema.MessageListResponse:
    """从 LangGraph 状态获取某个对话的所有消息。"""
    user_id = current_user.id
    conversation = await conversation_repo.get(user_id, conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError
    messages = await chat_service.list_messages(
        agents,
        sandbox,
        user_id,
        conversation_id,
    )
    logger.info(
        f"获取消息列表: conversation_id={conversation_id}, count={len(messages)}"
    )
    return chat_schema.MessageListResponse(messages=messages)


@router.get(
    "/{conversation_id}/subagents/{analysis_id}/{agent_type}/{session_id}/"
    "runs/{delegation_id}/messages"
)
async def api_get_subagent_messages(
    conversation_id: UUID,
    analysis_id: str,
    agent_type: AgentType,
    session_id: str,
    delegation_id: str,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    agents: AgentManagerDep,
) -> chat_schema.SubagentMessageListResponse:
    """读取一次 Specialist delegation 的公开工作消息。"""
    user_id = current_user.id
    conversation = await conversation_repo.get(user_id, conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError
    try:
        activity = await chat_service.get_subagent_activity(
            agents,
            user_id,
            conversation_id,
            analysis_id,
            agent_type,
            session_id,
            delegation_id,
        )
    except ValueError as exc:
        raise chat_error.SubagentRunNotFoundError from exc
    if activity is None:
        raise chat_error.SubagentRunNotFoundError
    return activity


def _serialize_sse_event(event: chat_schema.ChatStreamEventPayload) -> str:
    """将聊天事件序列化为 SSE 数据帧。"""
    return f"data: {event.model_dump_json()}\n\n"


async def _stream_run_events(
    conversation_id: UUID,
    events: AsyncGenerator[chat_schema.ChatStreamEventPayload],
) -> AsyncIterator[str]:
    """把后台 Run 事件投影为 SSE；连接断开只取消当前订阅。"""
    next_message_task: asyncio.Future[chat_schema.ChatStreamEventPayload] | None = None
    try:
        next_message_task = asyncio.ensure_future(anext(events))
        while True:
            done, _ = await asyncio.wait(
                {next_message_task},
                timeout=_SSE_HEARTBEAT_SECONDS,
            )
            if not done:
                yield ": keep-alive\n\n"
                continue

            try:
                event = next_message_task.result()
            except StopAsyncIteration:
                break

            yield _serialize_sse_event(event)
            next_message_task = asyncio.ensure_future(anext(events))
    except asyncio.CancelledError:
        logger.info(f"SSE 订阅断开: conversation_id={conversation_id}")
        raise
    finally:
        if next_message_task is not None and not next_message_task.done():
            next_message_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await next_message_task
        await events.aclose()


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "model": chat_schema.ChatStreamEvent,
            "description": "每个 SSE data 帧均为 ChatStreamEvent JSON",
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/ChatStreamEvent"}
                }
            },
        }
    },
)
async def api_stream_chat(
    body: chat_schema.ChatStreamRequest,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
    lifecycle: ConversationLifecycleServiceDep,
    runs: ConversationRunServiceDep,
) -> StreamingResponse:
    """启动后台对话回合并订阅 Agent 事件。"""
    user_id = current_user.id
    title_submission: tuple[UUID, str, str] | None = None
    async with lifecycle.lock(user_id, body.conversation_id):
        async with conversation_repo.session.begin():
            conversation = await conversation_repo.get(user_id, body.conversation_id)
            if conversation is None:
                raise chat_error.ConversationNotFoundError

            user_text = "\n".join(
                part.text
                for part in body.message.parts
                if isinstance(part, chat_schema.TextContent)
            ).strip()
            if (
                conversation.title_pending
                and conversation.title_source is None
                and user_text
            ):
                conversation = await conversation_repo.claim_title_generation(
                    conversation,
                    title=initial_conversation_title(user_text),
                    source=user_text,
                )
                title_submission = (
                    conversation.id,
                    conversation.title,
                    user_text,
                )
            elif conversation.is_draft:
                await conversation_repo.update(conversation, is_draft=False)
            else:
                await conversation_repo.update(conversation)

        if title_submission is not None:
            conversation_id, expected_title, source = title_submission
            try:
                enqueue_conversation_title(
                    user_id,
                    conversation_id,
                    expected_title,
                    source,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "提交会话标题任务失败，等待定时补偿: "
                    f"conversation_id={conversation_id}"
                )

    context.user_id_ctx.set(str(user_id))
    try:
        events = await runs.start_turn(user_id, body.conversation_id, body.message)
    except ActiveRunConflict as exc:
        raise chat_error.ConversationRunAlreadyActiveError from exc
    return StreamingResponse(
        _stream_run_events(
            body.conversation_id,
            events,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{conversation_id}/resume", response_class=StreamingResponse)
async def api_resume_chat(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
    agents: AgentManagerDep,
    runs: ConversationRunServiceDep,
) -> StreamingResponse:
    """从中断的 Planner Checkpoint 继续当前用户回合。"""
    user_id = current_user.id
    conversation = await conversation_repo.get(user_id, conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError
    if not await chat_service.can_resume_agent_turn(
        agents,
        user_id,
        conversation_id,
    ):
        raise chat_error.ConversationNotResumableError
    context.user_id_ctx.set(str(user_id))
    try:
        events = await runs.resume_turn(user_id, conversation_id)
    except ActiveRunConflict as exc:
        raise chat_error.ConversationRunAlreadyActiveError from exc
    return StreamingResponse(
        _stream_run_events(
            conversation_id,
            events,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{conversation_id}/run")
async def api_get_conversation_run_status(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
    runs: ConversationRunServiceDep,
) -> chat_schema.ConversationRunStatusResponse:
    """查询 Conversation 是否有正在后台执行的 Planner Run。"""
    user_id = current_user.id
    if await conversation_repo.get(user_id, conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    return chat_schema.ConversationRunStatusResponse(
        running=await runs.is_running(user_id, conversation_id)
    )


@router.get("/{conversation_id}/events", response_class=StreamingResponse)
async def api_subscribe_conversation_run(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
    runs: ConversationRunServiceDep,
) -> StreamingResponse:
    """订阅已经启动的后台 Planner Run。"""
    user_id = current_user.id
    if await conversation_repo.get(user_id, conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    context.user_id_ctx.set(str(user_id))
    events = await runs.subscribe(user_id, conversation_id)
    return StreamingResponse(
        _stream_run_events(conversation_id, events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{conversation_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def api_stop_conversation_run(
    conversation_id: UUID,
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
    runs: ConversationRunServiceDep,
) -> Response:
    """由用户显式停止 Conversation 当前的 Planner Run。"""
    user_id = current_user.id
    if await conversation_repo.get(user_id, conversation_id) is None:
        raise chat_error.ConversationNotFoundError
    await runs.stop(user_id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
