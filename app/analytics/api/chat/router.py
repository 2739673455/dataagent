"""对话管理、语义召回与 Agent SSE 流式交互路由"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.analytics import errors as chat_error
from app.analytics.agents.manager import AgentManager
from app.analytics.api.chat import schemas as chat_schema
from app.analytics.api.chat.dependencies import (
    ConversationPGRepoDep,
)
from app.analytics.api.dependencies import (
    AgentManagerDep,
    ConversationLifecycleServiceDep,
    SandboxManagerDep,
)
from app.analytics.services import chat as chat_service
from app.analytics.services.conversation_title import (
    initial_conversation_title,
)
from app.analytics.tasks import (
    enqueue_conversation_deletion,
    enqueue_conversation_title,
)
from app.identity.api.auth.dependencies import AnalysisUserDep, CurrentUserDep
from app.sandbox.manager import DockerSandboxManager
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
    """创建新对话"""
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
    )


@router.post("/delete")
async def api_delete_conversations(
    body: chat_schema.DeleteConversationRequest,
    current_user: CurrentUserDep,
    lifecycle: ConversationLifecycleServiceDep,
) -> None:
    """删除对话"""
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
    """幂等删除当前用户主动放弃的草稿会话"""
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
    """修改对话信息"""
    user_id = current_user.id

    async with conversation_repo.session.begin():
        # 检查对话是否存在且属于当前用户
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
) -> chat_schema.ConversationListResponse:
    """获取所有对话"""
    user_id = current_user.id
    conversations = await conversation_repo.list_by_user(user_id)
    logger.info(f"获取对话列表: conversation_ids={[item.id for item in conversations]}")
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
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
    agents: AgentManagerDep,
) -> chat_schema.MessageListResponse:
    """从 LangGraph 状态获取某个对话的所有消息"""
    user_id = current_user.id
    conversation = await conversation_repo.get(user_id, conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError

    messages = await chat_service.list_messages(
        agents,
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
    """读取一次 Specialist delegation 的公开工作消息"""
    user_id = current_user.id
    conversation = await conversation_repo.get(user_id, conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError
    try:
        messages = await chat_service.list_subagent_messages(
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
    if messages is None:
        raise chat_error.SubagentRunNotFoundError
    return chat_schema.SubagentMessageListResponse(messages=messages)


def _serialize_sse_event(event: chat_schema.ChatStreamEventPayload) -> str:
    """将聊天事件序列化为 SSE 数据帧"""
    return f"data: {event.model_dump_json()}\n\n"


async def _stream_agent_response(
    agents: AgentManager,
    sandbox: DockerSandboxManager,
    user_id: int,
    conversation_id: UUID,
    user_message: chat_schema.UserMessageRequest,
) -> AsyncIterator[str]:
    """流式执行单轮 Agent 对话"""
    cancel = asyncio.Event()
    responses = chat_service.run_agent_turn(
        agents,
        sandbox,
        user_id,
        conversation_id,
        user_message,
        cancel,
    )
    next_message_task: asyncio.Task[chat_schema.ChatStreamEventPayload] | None = None
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
                event = next_message_task.result()
            except StopAsyncIteration:
                break

            yield _serialize_sse_event(event)
            next_message_task = asyncio.create_task(anext(responses))
    except asyncio.CancelledError:
        logger.info(f"SSE 连接断开: conversation_id={conversation_id}")
        raise
    except Exception:  # noqa: BLE001
        logger.exception(f"智能体执行异常: conversation_id={conversation_id}")
        yield _serialize_sse_event(
            chat_schema.ChatStreamErrorEvent(
                type="error", content="模型调用失败，请稍后重试。"
            )
        )
    else:
        yield _serialize_sse_event(chat_schema.ChatStreamDoneEvent(type="done"))
    finally:
        cancel.set()
        if next_message_task is not None and not next_message_task.done():
            next_message_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await next_message_task
        await responses.aclose()


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
    agents: AgentManagerDep,
    sandbox: SandboxManagerDep,
) -> StreamingResponse:
    """通过 SSE 执行单轮对话并流式返回 Agent 事件"""
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
    return StreamingResponse(
        _stream_agent_response(
            agents,
            sandbox,
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
