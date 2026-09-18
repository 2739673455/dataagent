"""LangGraph 最新 Checkpoint 的轻量只读投影。"""

from __future__ import annotations

import operator
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from deepagents._messages_reducer import _messages_delta_reducer
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph._internal._constants import ERROR, INTERRUPT, NULL_TASK_ID, TASKS
from langgraph.channels import BinaryOperatorAggregate, EphemeralValue, Topic
from langgraph.channels.base import BaseChannel
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.constants import START
from langgraph.pregel._algo import prepare_next_tasks, task_path_str
from langgraph.pregel._checkpoint import achannels_from_checkpoint
from langgraph.pregel._read import PregelNode
from langgraph.types import PregelTask, Send

_READ_CHANNELS = frozenset({"messages", "delegation_records"})
_BRANCH_PREFIX = "branch:to:"


@dataclass(frozen=True, slots=True)
class CheckpointState:
    """历史展示和委派恢复所需的业务状态投影，不是完整图状态。"""

    values: Mapping[str, object]
    next_nodes: tuple[str, ...]
    updated_at: datetime | None


def _read_only_node(_: object) -> None:
    """任务描述只用于读取，不允许执行。"""
    raise RuntimeError("Checkpoint 只读任务不能执行")


def _channel_specs(
    saved: CheckpointTuple, *, include_business: bool
) -> dict[str, BaseChannel]:
    """还原 Assistant 使用的业务通道、StateGraph 分支触发器和 Send 队列。"""
    specs: dict[str, BaseChannel] = {
        TASKS: Topic(Send, accumulate=False),
        START: EphemeralValue(Any, guard=False),
    }
    if include_business:
        specs.update(
            messages=DeltaChannel(cast(Any, _messages_delta_reducer), list),
            delegation_records=BinaryOperatorAggregate(dict, operator.or_),
        )
    for channel in saved.checkpoint["channel_versions"]:
        if channel.startswith(_BRANCH_PREFIX):
            specs[channel] = EphemeralValue(Any, guard=False)
    for name, channel in specs.items():
        channel.key = name
    return specs


def _pending_tasks(
    saved: CheckpointTuple, channels: Mapping[str, BaseChannel]
) -> dict[str, PregelTask]:
    """由框架生成 PULL/PUSH 任务 ID 和路径，不按写入顺序猜测所属节点。

    Assistant 的 create_agent 图使用每节点一个 branch:to 触发器和 Send。
    这里仅构造读取任务所需的描述，不构建模型/工具，也不执行节点。
    不支持任意 Pregel 拓扑；框架适配集中在此处并由真实图对照测试约束。
    """
    triggers = {
        name.removeprefix(_BRANCH_PREFIX): [name]
        for name in channels
        if name.startswith(_BRANCH_PREFIX)
    }
    triggers[START] = [START]
    if channels[TASKS].is_available():
        for packet in channels[TASKS].get():
            if isinstance(packet, Send):
                triggers.setdefault(packet.node, [])
    # Send 目标的分支通道可能尚未出现版本；无此通道时没有 PULL 触发。
    processes = {
        name: PregelNode(
            channels=[], triggers=names, bound=RunnableLambda(_read_only_node)
        )
        for name, names in triggers.items()
    }
    step = saved.metadata.get("step", -1) + 1
    return prepare_next_tasks(
        saved.checkpoint,
        saved.pending_writes or [],
        processes,
        channels,
        {},
        saved.config,
        step,
        step + 2,
        for_execution=False,
    )


def _task_writes(saved: CheckpointTuple) -> dict[str, list[tuple[str, Any]]]:
    """控制消息不代表任务已完成，其余写入同时用于完成判断与业务投影。"""
    writes: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for task_id, channel, value in saved.pending_writes or ():
        if channel not in {ERROR, INTERRUPT}:
            writes[task_id].append((channel, value))
    return writes


def _next_nodes(
    tasks: Mapping[str, PregelTask],
    writes: Mapping[str, list[tuple[str, Any]]],
) -> tuple[str, ...]:
    return tuple(task.name for task in tasks.values() if not writes.get(task.id))


def _apply_business_writes(
    channels: Mapping[str, BaseChannel], writes: list[tuple[str, Any]]
) -> None:
    """按框架排序后的任务结果整批更新 reducer，保留 Overwrite/消息删除语义。"""
    updates: dict[str, list[Any]] = defaultdict(list)
    for channel, value in writes:
        if channel in _READ_CHANNELS:
            updates[channel].append(value)
    for channel, values in updates.items():
        channels[channel].update(values)


class CheckpointStateReader:
    """不依赖 CompiledGraph 运行资源读取最新 Assistant 状态。"""

    def __init__(self, checkpointer: BaseCheckpointSaver) -> None:
        """绑定 LangGraph Checkpoint 持久化实现。"""
        self._checkpointer = checkpointer

    async def has_pending_tasks(self, config: RunnableConfig) -> bool:
        """只还原调度通道判断能否继续，不查询或重放消息 Delta 历史。"""
        saved = await self._checkpointer.aget_tuple(config)
        if saved is None:
            return False
        channels, _ = await achannels_from_checkpoint(
            _channel_specs(saved, include_business=False), saved.checkpoint
        )
        return bool(_next_nodes(_pending_tasks(saved, channels), _task_writes(saved)))

    async def read(self, config: RunnableConfig) -> CheckpointState:
        """读取业务状态，仅合并有效任务的 pending writes 并排除已完成任务。"""
        saved = await self._checkpointer.aget_tuple(config)
        if saved is None:
            return CheckpointState(values={}, next_nodes=(), updated_at=None)
        channels, _ = await achannels_from_checkpoint(
            _channel_specs(saved, include_business=True),
            saved.checkpoint,
            saver=self._checkpointer,
            config=saved.config,
        )
        tasks = _pending_tasks(saved, channels)
        writes_by_task = _task_writes(saved)
        # 与 aget_state 一致：先应用外部更新，再按任务路径合并当前步骤的结果。
        _apply_business_writes(channels, writes_by_task[NULL_TASK_ID])
        _apply_business_writes(
            channels,
            [
                write
                for task in sorted(
                    tasks.values(), key=lambda task: task_path_str(task.path[:3])
                )
                for write in writes_by_task[task.id]
            ],
        )
        timestamp = saved.checkpoint.get("ts")
        try:
            updated_at = (
                datetime.fromisoformat(timestamp)
                if isinstance(timestamp, str)
                else None
            )
        except ValueError:
            updated_at = None
        return CheckpointState(
            values={
                name: channels[name].get()
                for name in _READ_CHANNELS
                if channels[name].is_available()
            },
            next_nodes=_next_nodes(tasks, writes_by_task),
            updated_at=updated_at,
        )
