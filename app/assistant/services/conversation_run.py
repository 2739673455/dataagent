"""与客户端连接解耦的 Conversation Agent 执行管理。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from uuid import UUID

from loguru import logger

from app.assistant.contracts import chat as chat_schema
from app.assistant.services import chat as chat_service
from app.assistant.services.contracts import (
    AgentRuntimeManager,
    ConversationFileInspector,
)

type ConversationRunKey = tuple[int, UUID]
type RunEvent = chat_schema.ChatStreamEventPayload

_REPLAY_EVENT_LIMIT = 512
_REPLAY_BYTE_LIMIT = 2 * 1024 * 1024
_SUBSCRIBER_QUEUE_LIMIT = 256
_DELTA_EVENT_TYPES = (
    chat_schema.ChatStreamThinkingEvent,
    chat_schema.ChatStreamMessageDeltaEvent,
    chat_schema.ChatStreamSubagentThinkingEvent,
    chat_schema.ChatStreamSubagentMessageDeltaEvent,
)


@dataclass(slots=True)
class _ConversationRun:
    """一个独立于 SSE 订阅者生命周期的 Planner Run。"""

    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    events: deque[RunEvent] = field(default_factory=deque)
    replay_bytes: int = 0
    subscribers: set[asyncio.Queue[RunEvent | None]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


class ActiveConversationRunError(RuntimeError):
    """同一 Conversation 已经存在运行中的 Planner Run。"""


class ConversationRunService:
    """后台执行 Planner Run，并向任意数量的 SSE 连接发布事件。"""

    def __init__(
        self,
        agents: AgentRuntimeManager,
        files: ConversationFileInspector,
    ) -> None:
        """绑定 Agent 执行依赖并初始化进程内 Run 注册表。"""
        self._agents = agents
        self._files = files
        self._runs: dict[ConversationRunKey, _ConversationRun] = {}
        self._lock = asyncio.Lock()

    async def start_turn(
        self,
        user_id: int,
        conversation_id: UUID,
        user_message: chat_schema.UserMessageRequest,
    ) -> AsyncGenerator[RunEvent]:
        """启动新用户回合并返回首个事件订阅。"""
        return await self._start(user_id, conversation_id, user_message)

    async def resume_turn(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[RunEvent]:
        """后台恢复中断回合并返回首个事件订阅。"""
        return await self._start(user_id, conversation_id, None)

    async def _start(
        self,
        user_id: int,
        conversation_id: UUID,
        user_message: chat_schema.UserMessageRequest | None,
    ) -> AsyncGenerator[RunEvent]:
        """原子注册后台 Run，并返回包含首订阅者的事件流。"""
        key = (user_id, conversation_id)
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_LIMIT
        )
        run = _ConversationRun()
        run.subscribers.add(queue)
        async with self._lock:
            existing = self._runs.get(key)
            if (
                existing is not None
                and existing.task is not None
                and not existing.task.done()
            ):
                raise ActiveConversationRunError
            self._runs[key] = run
            run.task = asyncio.create_task(
                self._execute(key, run, user_message),
                name=f"conversation-run:{user_id}:{conversation_id}",
            )
        return self._consume(run, queue, ())

    async def subscribe(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[RunEvent]:
        """订阅当前 Run；Run 已结束时立即返回 done。"""
        key = (user_id, conversation_id)
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_LIMIT
        )
        async with self._lock:
            run = self._runs.get(key)
            if run is None:
                return self._completed_subscription()
            replay = tuple(run.events)
            run.subscribers.add(queue)
        return self._consume(run, queue, replay)

    async def is_running(self, user_id: int, conversation_id: UUID) -> bool:
        """返回指定 Conversation 是否存在后台 Planner Run。"""
        async with self._lock:
            run = self._runs.get((user_id, conversation_id))
            return run is not None and run.task is not None and not run.task.done()

    async def running_conversation_ids(self, user_id: int) -> set[UUID]:
        """返回指定用户当前仍在后台执行的 Conversation ID。"""
        async with self._lock:
            return {
                conversation_id
                for (run_user_id, conversation_id), run in self._runs.items()
                if run_user_id == user_id
                and run.task is not None
                and not run.task.done()
            }

    async def stop(self, user_id: int, conversation_id: UUID) -> bool:
        """显式停止指定 Conversation 的 Planner Run。"""
        async with self._lock:
            run = self._runs.get((user_id, conversation_id))
            if run is None or run.task is None or run.task.done():
                return False
            run.cancel.set()
            task = run.task
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def _execute(
        self,
        key: ConversationRunKey,
        run: _ConversationRun,
        user_message: chat_schema.UserMessageRequest | None,
    ) -> None:
        """执行新回合或恢复回合，并把结果发布给全部订阅者。"""
        user_id, conversation_id = key
        responses = (
            chat_service.run_agent_turn(
                self._agents,
                self._files,
                user_id,
                conversation_id,
                user_message,
                run.cancel,
            )
            if user_message is not None
            else chat_service.resume_agent_turn(
                self._agents,
                self._files,
                user_id,
                conversation_id,
                run.cancel,
            )
        )
        try:
            async for event in responses:
                await self._publish(run, event)
        except asyncio.CancelledError:
            logger.info(f"智能体执行已停止: conversation_id={conversation_id}")
        except Exception:  # noqa: BLE001
            logger.exception(f"智能体执行异常: conversation_id={conversation_id}")
            await self._publish(
                run,
                chat_schema.ChatStreamErrorEvent(
                    type="error",
                    content="模型调用失败，请稍后重试。",
                ),
            )
        finally:
            run.cancel.set()
            try:
                await responses.aclose()
            finally:
                await self._finish(key, run)

    async def _publish(self, run: _ConversationRun, event: RunEvent) -> None:
        """按产生顺序缓存事件并广播给所有当前订阅者。"""
        async with self._lock:
            # 缓存快照与订阅登记共用一把锁：订阅者要么从 replay 得到该事件，
            # 要么已进入 subscribers 接收实时事件，不能漏收或重复接收。
            self._cache_event(run, event)
            subscribers = tuple(run.subscribers)
        for queue in subscribers:
            if not self._offer_event(queue, event):
                await self._drop_slow_subscriber(run, queue)

    async def _finish(self, key: ConversationRunKey, run: _ConversationRun) -> None:
        """结束 Run 并通知订阅者关闭事件流。"""
        done = chat_schema.ChatStreamDoneEvent(type="done")
        async with self._lock:
            if self._runs.get(key) is run:
                self._runs.pop(key, None)
            self._cache_event(run, done)
            subscribers = tuple(run.subscribers)
        for queue in subscribers:
            if not self._offer_event(queue, done) or not self._offer_event(queue, None):
                await self._drop_slow_subscriber(run, queue)

    @staticmethod
    def _event_size(event: RunEvent) -> int:
        """估算一项 replay 事件序列化后的 UTF-8 字节数。"""
        return len(event.model_dump_json().encode("utf-8"))

    @staticmethod
    def _merge_delta(previous: RunEvent, event: RunEvent) -> RunEvent | None:
        """合并同一消息连续产生的正文或思考增量。"""
        if (
            not isinstance(previous, _DELTA_EVENT_TYPES)
            or not isinstance(event, _DELTA_EVENT_TYPES)
            or type(previous) is not type(event)
        ):
            return None
        if event.reset:
            return None
        identity_fields = ("message_id", "delegation_id", "parent_tool_call_id")
        if any(
            getattr(previous, field, None) != getattr(event, field, None)
            for field in identity_fields
        ):
            return None
        return previous.model_copy(update={"delta": previous.delta + event.delta})

    def _cache_event(self, run: _ConversationRun, event: RunEvent) -> None:
        """将事件写入有数量和字节边界的重放窗口。"""
        if run.events:
            merged = self._merge_delta(run.events[-1], event)
            if merged is not None:
                previous = run.events.pop()
                run.replay_bytes -= self._event_size(previous)
                event = merged
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

    async def _drop_slow_subscriber(
        self,
        run: _ConversationRun,
        queue: asyncio.Queue[RunEvent | None],
    ) -> None:
        """断开无法跟上实时事件的订阅者，避免其占用无界内存。"""
        async with self._lock:
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
            async with self._lock:
                run.subscribers.discard(queue)

    async def _completed_subscription(self) -> AsyncGenerator[RunEvent]:
        """构造已结束 Run 的空订阅。"""
        yield chat_schema.ChatStreamDoneEvent(type="done")

    async def close(self) -> None:
        """应用停止时取消进程内全部后台 Run。"""
        async with self._lock:
            runs = tuple(self._runs.values())
            tasks = tuple(
                run.task for run in runs if run.task is not None and not run.task.done()
            )
            for run in runs:
                run.cancel.set()
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
