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
)
from app.assistant.messages import (
    MessageDeltaParser,
    project_messages,
    schema_to_human_message,
    update_messages,
)
from app.assistant.models import chat as chat_schema

if TYPE_CHECKING:
    from app.assistant.agents.manager import AgentManager
    from app.sandbox.manager import DockerSandboxManager

type ConversationRunKey = tuple[int, UUID]
type RunEvent = chat_schema.ChatStreamEventPayload

_REPLAY_EVENT_LIMIT = 512
_REPLAY_BYTE_LIMIT = 2 * 1024 * 1024


@dataclass(slots=True)
class _ConversationRun:
    """Run 是一次启动或恢复产生的进程内执行实例。

    独立于 SSE 连接存活，持有后台任务、共享事件缓存和更新通知；
    用户 Turn 的可恢复状态由 LangGraph 检查点保存。"""

    ready: asyncio.Future[Exception | None]
    events: deque[RunEvent] = field(default_factory=deque)
    replay_bytes: int = 0
    offset: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    finished: bool = False
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
        run = _ConversationRun(ready=asyncio.get_running_loop().create_future())
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
            if not run.ready.done():
                run.task.cancel()
                await asyncio.gather(run.task, return_exceptions=True)
            raise
        return self._consume(run, 0)

    def subscribe(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> AsyncGenerator[RunEvent]:
        """订阅当前 Run；Run 已结束时立即返回 done。"""
        key = (user_id, conversation_id)
        run = self._runs.get(key)
        if run is None:
            return self._completed_subscription()
        return self._consume(run, run.offset)

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
                # 受理失败交由请求返回 HTTP 错误；执行失败通过 SSE 通知。
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
        """缓存事件并唤醒读取者；淘汰窗口之外的旧事件。"""
        run.events.append(event)
        run.replay_bytes += len(event.model_dump_json().encode("utf-8"))
        while run.events and (
            len(run.events) > _REPLAY_EVENT_LIMIT
            or run.replay_bytes > _REPLAY_BYTE_LIMIT
        ):
            run.replay_bytes -= len(
                run.events.popleft().model_dump_json().encode("utf-8")
            )
            run.offset += 1
        run.changed.set()

    def _finish(self, key: ConversationRunKey, run: _ConversationRun) -> None:
        """任务退出后移除运行记录并通知订阅者。"""
        if self._runs.get(key) is run:
            self._runs.pop(key, None)
        if not run.ready.done():
            run.ready.set_result(ConversationBusyError(detail="对话在受理完成前已停止"))
        run.finished = True
        run.changed.set()

    async def _consume(
        self,
        run: _ConversationRun,
        cursor: int,
    ) -> AsyncGenerator[RunEvent]:
        """按连接自己的位置读取共享缓存，断开连接不影响后台执行。"""
        while True:
            if cursor < run.offset:
                yield chat_schema.ChatStreamErrorEvent(
                    type="error",
                    content="事件流消费速度过慢，请重新连接以恢复最新状态。",
                )
                return
            if cursor < run.offset + len(run.events):
                event = run.events[cursor - run.offset]
                cursor += 1
                yield event
            elif run.finished:
                yield chat_schema.ChatStreamDoneEvent(type="done")
                return
            else:
                # 检查位置到开始等待之间不让出执行权，避免遗漏更新通知。
                run.changed.clear()
                await run.changed.wait()

    async def _completed_subscription(self) -> AsyncGenerator[RunEvent]:
        """构造已结束 Run 的空订阅。"""
        yield chat_schema.ChatStreamDoneEvent(type="done")


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
    deltas = MessageDeltaParser()
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
                yield chat_schema.ChatStreamMessageEvent(
                    type="message",
                    message=response,
                )

    logger.info(f"智能体回合结束: conversation_id={conversation_id}")
