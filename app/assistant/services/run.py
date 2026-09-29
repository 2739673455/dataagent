"""会话后台运行、Planner 回合执行与聊天事件订阅。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from langchain_core.messages import BaseMessage
from langgraph.types import StreamPart
from loguru import logger

from app.assistant.agents.context import (
    PlannerTurnContext,
    SubagentStatusActivity,
    build_planner_config,
)
from app.assistant.errors import (
    ConversationBusyError,
    ConversationRunConflictError,
    PlannerContinuationLimitError,
)
from app.assistant.messages.projection import project_messages, schema_to_human_message
from app.assistant.messages.stream import MessageDeltaParser, update_messages
from app.assistant.models import chat as chat_schema
from app.shared.config.app_config import cfg

if TYPE_CHECKING:
    from app.assistant.agents.manager import AgentManager
    from app.sandbox.manager import DockerSandboxManager

type ConversationRunKey = tuple[int, UUID]
type RunEvent = chat_schema.ChatStreamEventPayload

_REPLAY_EVENT_LIMIT = 512
_REPLAY_BYTE_LIMIT = 2 * 1024 * 1024
_SUBSCRIBER_QUEUE_LIMIT = 256
_DELTA_EVENT_TYPES = (
    chat_schema.ChatStreamThinkingEvent,
    chat_schema.ChatStreamMessageDeltaEvent,
)


@dataclass(slots=True)
class _ConversationRun:
    """Run 是一次启动或恢复产生的进程内执行实例。

    独立于 SSE 连接存活，持有后台任务、事件缓存和订阅者；
    用户 Turn 的可恢复状态由 LangGraph 检查点保存。"""

    ready: asyncio.Future[Exception | None]
    events: deque[RunEvent] = field(default_factory=deque)
    replay_bytes: int = 0
    subscribers: set[asyncio.Queue[RunEvent | None]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


class ConversationRunService:
    """在单 Web 事件循环中管理后台 Run 和 SSE 订阅。

    注册、缓存和订阅修改均同步完成；仅准备、执行和取消等待让出执行权。
    """

    def __init__(
        self,
        agents: AgentManager,
        files: DockerSandboxManager,
    ) -> None:
        """绑定 Agent 执行依赖并初始化进程内 Run 注册表。"""
        self._agents = agents
        self._files = files
        self._runs: dict[ConversationRunKey, _ConversationRun] = {}

    async def start(
        self,
        user_id: int,
        conversation_id: UUID,
        user_message: chat_schema.UserMessageRequest | None,
        *,
        prepare: Callable[[], Awaitable[None]],
    ) -> AsyncGenerator[RunEvent]:
        """原子注册后台 Run；消息为 None 时恢复回合，返回首订阅者事件流。"""
        key = (user_id, conversation_id)
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_LIMIT
        )
        run = _ConversationRun(ready=asyncio.get_running_loop().create_future())
        run.subscribers.add(queue)
        existing = self._runs.get(key)
        if (
            existing is not None
            and existing.task is not None
            and not existing.task.done()
        ):
            raise ConversationRunConflictError
        self._runs[key] = run
        run.task = asyncio.create_task(
            self._execute(key, run, user_message, prepare),
            name=f"conversation-run:{user_id}:{conversation_id}",
        )
        # Task 完成回调也覆盖协程首次执行前被取消的情况。
        run.task.add_done_callback(lambda _: self._finish(key, run))
        try:
            failure = await asyncio.shield(run.ready)
            if failure is not None:
                await asyncio.gather(run.task, return_exceptions=True)
                raise failure
        except BaseException:
            run.subscribers.discard(queue)
            if not run.ready.done():
                run.task.cancel()
                await asyncio.gather(run.task, return_exceptions=True)
            raise
        return self._consume(run, queue, ())

    def subscribe(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[RunEvent]:
        """订阅当前 Run；Run 已结束时立即返回 done。"""
        key = (user_id, conversation_id)
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_LIMIT
        )
        run = self._runs.get(key)
        if run is None:
            return self._completed_subscription()
        replay = tuple(run.events)
        run.subscribers.add(queue)
        return self._consume(run, queue, replay)

    def is_running(self, user_id: int, conversation_id: UUID) -> bool:
        """返回指定 Conversation 是否存在后台 Planner Run。"""
        run = self._runs.get((user_id, conversation_id))
        return run is not None and run.task is not None and not run.task.done()

    def running_conversation_ids(self, user_id: int) -> set[UUID]:
        """返回指定用户当前仍在后台执行的 Conversation ID。"""
        return {
            conversation_id
            for (run_user_id, conversation_id), run in self._runs.items()
            if run_user_id == user_id and run.task is not None and not run.task.done()
        }

    async def stop(self, user_id: int, conversation_id: UUID) -> bool:
        """显式停止指定 Conversation 的 Planner Run。"""
        run = self._runs.get((user_id, conversation_id))
        if run is None or run.task is None or run.task.done():
            return False
        task = run.task
        if not task.cancelling():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def _execute(
        self,
        key: ConversationRunKey,
        run: _ConversationRun,
        user_message: chat_schema.UserMessageRequest | None,
        prepare: Callable[[], Awaitable[None]],
    ) -> None:
        """执行新回合或恢复回合，并把结果发布给全部订阅者。"""
        user_id, conversation_id = key
        try:
            await prepare()
            run.ready.set_result(None)
            responses = run_agent_turn(
                self._agents,
                self._files,
                PlannerTurnContext(
                    user_id,
                    conversation_id,
                    cfg.agent.orchestration.max_continuations,
                ),
                user_message,
            )
            try:
                async for event in responses:
                    self._publish(run, event)
            finally:
                await responses.aclose()
        except asyncio.CancelledError:
            logger.info(f"智能体执行已停止: conversation_id={conversation_id}")
        except Exception as exc:  # noqa: BLE001
            if not run.ready.done():
                # HTTP 受理阶段的业务错误直接返回调用方，不伪装成模型 SSE 错误。
                run.ready.set_result(exc)
            else:
                logger.exception(f"智能体执行异常: conversation_id={conversation_id}")
                self._publish(
                    run,
                    chat_schema.ChatStreamErrorEvent(
                        type="error",
                        content="模型调用失败，请稍后重试。",
                    ),
                )

    def _publish(self, run: _ConversationRun, event: RunEvent) -> None:
        """按产生顺序缓存事件并广播给所有当前订阅者。"""
        self._cache_event(run, event)
        subscribers = tuple(run.subscribers)
        for queue in subscribers:
            if not self._offer_event(queue, event):
                self._drop_slow_subscriber(run, queue)

    def _finish(self, key: ConversationRunKey, run: _ConversationRun) -> None:
        """仅由 Task 完成回调收尾；同步执行，不产生可重复进入的 await 窗口。"""
        # 回调只执行一次，并且在执行流及其清理完全退出后通知订阅者。
        if self._runs.get(key) is run:
            self._runs.pop(key, None)
        if not run.ready.done():
            run.ready.set_result(ConversationBusyError(detail="对话在受理完成前已停止"))
        done = chat_schema.ChatStreamDoneEvent(type="done")
        self._cache_event(run, done)
        for queue in tuple(run.subscribers):
            if not self._offer_event(queue, done) or not self._offer_event(queue, None):
                self._drop_slow_subscriber(run, queue)

    @staticmethod
    def _event_size(event: RunEvent) -> int:
        """估算一项 replay 事件序列化后的 UTF-8 字节数。"""
        return len(event.model_dump_json().encode("utf-8"))

    def _cache_event(self, run: _ConversationRun, event: RunEvent) -> None:
        """将事件写入有数量和字节边界的重放窗口。"""
        if run.events:
            previous = run.events[-1]
            if (
                isinstance(previous, _DELTA_EVENT_TYPES)
                and isinstance(event, _DELTA_EVENT_TYPES)
                and type(previous) is type(event)
                and not event.reset
                and previous.message_id == event.message_id
            ):
                run.events.pop()
                run.replay_bytes -= self._event_size(previous)
                event = previous.model_copy(
                    update={"delta": previous.delta + event.delta}
                )
        run.events.append(event)
        run.replay_bytes += self._event_size(event)
        while run.events and (
            len(run.events) > _REPLAY_EVENT_LIMIT
            or run.replay_bytes > _REPLAY_BYTE_LIMIT
        ):
            run.replay_bytes -= self._event_size(run.events.popleft())

    @staticmethod
    def _offer_event(
        queue: asyncio.Queue[RunEvent | None],
        event: RunEvent | None,
    ) -> bool:
        """向订阅队列非阻塞写入事件，队列满时由调用方断开慢消费者。"""
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            return False
        return True

    def _drop_slow_subscriber(
        self,
        run: _ConversationRun,
        queue: asyncio.Queue[RunEvent | None],
    ) -> None:
        """断开无法跟上实时事件的订阅者，避免其占用无界内存。"""
        if queue not in run.subscribers:
            return
        run.subscribers.discard(queue)
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(
            chat_schema.ChatStreamErrorEvent(
                type="error",
                content="事件流消费速度过慢，请重新连接以恢复最新状态。",
            )
        )
        queue.put_nowait(None)

    async def _consume(
        self,
        run: _ConversationRun,
        queue: asyncio.Queue[RunEvent | None],
        replay: tuple[RunEvent, ...],
    ) -> AsyncGenerator[RunEvent]:
        """读取一次订阅；订阅取消只移除订阅者，不影响后台 Run。"""
        try:
            for event in replay:
                yield event
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            run.subscribers.discard(queue)

    async def _completed_subscription(self) -> AsyncGenerator[RunEvent]:
        """构造已结束 Run 的空订阅。"""
        yield chat_schema.ChatStreamDoneEvent(type="done")

    async def close(self) -> None:
        """应用停止时取消进程内全部后台 Run。"""
        runs = tuple(self._runs.values())
        tasks = tuple(
            run.task for run in runs if run.task is not None and not run.task.done()
        )
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def run_agent_turn(
    agents: AgentManager,
    files: DockerSandboxManager,
    turn_context: PlannerTurnContext,
    user_message: chat_schema.UserMessageRequest | None,
) -> AsyncGenerator[chat_schema.ChatStreamEventPayload]:
    """执行新回合或从待执行 Checkpoint 恢复同一回合。"""
    user_id, conversation_id = turn_context.user_id, turn_context.conversation_id
    input_messages: list[BaseMessage] | None = (
        [schema_to_human_message(user_message)] if user_message is not None else None
    )
    logger.info(
        f"智能体回合开始: conversation_id={conversation_id}, "
        f"resume={user_message is None}, "
        f"parts={len(user_message.parts) if user_message is not None else 0}"
    )
    planner = await agents.create_planner(user_id, conversation_id)
    continuation_count = 0
    deltas = MessageDeltaParser()
    while True:
        last_finish_reason: str | None = None

        # LangGraph 的重载声明返回 AsyncIterator，实际 astream 是异步生成器。
        async with aclosing(
            cast(
                AsyncGenerator[StreamPart[Any, Any]],
                planner.astream(
                    input={"messages": input_messages}
                    if input_messages is not None
                    else None,
                    config=build_planner_config(
                        turn_context.user_id, turn_context.conversation_id
                    ),
                    stream_mode=["updates", "custom", "messages"],
                    version="v2",
                ),
            )
        ) as stream:
            async for chunk in stream:
                if chunk.get("type") == "custom":
                    activity = chunk.get("data")
                    if isinstance(activity, SubagentStatusActivity):
                        yield chat_schema.ChatStreamSubagentStatusEvent(
                            type="subagent_status",
                            delegation_id=activity.delegation_id,
                            agent_type=activity.agent_type,
                            status=activity.status,
                        )
                    continue
                if chunk.get("type") == "messages":
                    data = chunk.get("data")
                    if (
                        isinstance(data, tuple)
                        and data[1].get("lc_agent_name", "planner") != "planner"
                    ):
                        continue
                    for kind, delta in deltas.parse(chunk.get("data")):
                        if kind == "thinking":
                            yield chat_schema.ChatStreamThinkingEvent(
                                type="thinking", **delta
                            )
                        else:
                            yield chat_schema.ChatStreamMessageDeltaEvent(
                                type="message_delta", **delta
                            )
                    continue
                if chunk.get("type") != "updates":
                    continue
                responses = await project_messages(
                    update_messages(chunk.get("data")),
                    files,
                    user_id,
                    conversation_id,
                )
                for response in responses:
                    last_finish_reason = response.finish_reason
                    yield chat_schema.ChatStreamMessageEvent(
                        type="message",
                        message=response,
                    )

        if last_finish_reason is None or last_finish_reason == "stop":
            break
        if continuation_count >= turn_context.max_continuations:
            raise PlannerContinuationLimitError(
                turn_context.max_continuations,
                last_finish_reason,
            )

        continuation_count += 1
        # 空增量会保留 Checkpointer 中的已有状态并继续生成。
        input_messages = []

    logger.info(f"智能体回合结束: conversation_id={conversation_id}")
