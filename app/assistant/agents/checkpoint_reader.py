"""LangGraph 最新 Checkpoint 的轻量只读投影。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from deepagents._messages_reducer import _messages_delta_reducer
from langchain_core.runnables import RunnableConfig
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.constants import ERROR, INTERRUPT


@dataclass(frozen=True, slots=True)
class CheckpointState:
    """调用方读取历史和恢复状态所需的最新物化状态。"""

    values: Mapping[str, object]
    next_nodes: tuple[str, ...]
    updated_at: datetime | None


def _merge_pending_value(
    values: dict[str, object],
    channel: str,
    value: object,
) -> None:
    """应用当前 Assistant 状态中实际使用的 reducer 语义。"""
    if channel == "messages":
        values[channel] = _messages_delta_reducer(
            cast(Any, values.get(channel)),
            [cast(Any, value)],
        )
    elif channel == "delegation_records":
        current = values.get(channel)
        values[channel] = {
            **(current if isinstance(current, Mapping) else {}),
            **(value if isinstance(value, Mapping) else {}),
        }
    else:
        values[channel] = value


def _next_nodes(checkpoint: Mapping[str, object]) -> tuple[str, ...]:
    """从尚未被目标节点消费的 branch trigger 恢复待执行节点。"""
    channel_values = checkpoint.get("channel_values")
    channel_versions = checkpoint.get("channel_versions")
    versions_seen = checkpoint.get("versions_seen")
    if not (
        isinstance(channel_values, Mapping)
        and isinstance(channel_versions, Mapping)
        and isinstance(versions_seen, Mapping)
    ):
        return ()

    nodes: list[str] = []
    for channel in channel_values:
        if not isinstance(channel, str) or not channel.startswith("branch:to:"):
            continue
        node = channel.removeprefix("branch:to:")
        seen = versions_seen.get(node)
        # 目标节点记录的已见版本落后于 trigger 版本时，该分支仍等待执行。
        if not isinstance(seen, Mapping) or seen.get(channel) != channel_versions.get(
            channel
        ):
            nodes.append(node)
    return tuple(sorted(nodes))


class CheckpointStateReader:
    """不依赖 CompiledGraph 运行资源读取最新 Assistant 状态。"""

    def __init__(self, checkpointer: BaseCheckpointSaver) -> None:
        """绑定 LangGraph Checkpoint 持久化实现。"""
        self._checkpointer = checkpointer

    async def read(self, config: RunnableConfig) -> CheckpointState:
        """读取最新 Checkpoint，并合并已经提交的节点 pending writes。"""
        saved = await self._checkpointer.aget_tuple(config)
        if saved is None:
            return CheckpointState(values={}, next_nodes=(), updated_at=None)

        checkpoint = saved.checkpoint
        channel_values = checkpoint.get("channel_values")
        values = dict(channel_values) if isinstance(channel_values, Mapping) else {}
        channel_versions = checkpoint.get("channel_versions")
        if isinstance(channel_versions, Mapping) and "messages" in channel_versions:
            history = await self._checkpointer.aget_delta_channel_history(
                config=saved.config,
                channels=["messages"],
            )
            message_history = history["messages"]
            message_channel = DeltaChannel(
                cast(Any, _messages_delta_reducer),
                list,
                snapshot_frequency=50,
            )
            message_channel.key = "messages"
            message_channel = message_channel.from_checkpoint(
                values.get("messages", message_history.get("seed"))
            )
            message_channel.replay_writes(message_history["writes"])
            values["messages"] = message_channel.get() or []
        # 节点结果可能已经写入 pending_writes、尚未来得及形成下一份 Checkpoint；
        # 只读投影必须合并这些值，才能与 CompiledGraph.aget_state() 保持一致。
        for _task_id, channel, value in saved.pending_writes or ():
            if channel in {ERROR, INTERRUPT}:
                # 控制通道描述任务状态，不属于业务 State channel values。
                continue
            _merge_pending_value(values, channel, value)

        timestamp = checkpoint.get("ts")
        try:
            updated_at = (
                datetime.fromisoformat(timestamp)
                if isinstance(timestamp, str)
                else None
            )
        except ValueError:
            updated_at = None
        return CheckpointState(
            values=values,
            next_nodes=_next_nodes(checkpoint),
            updated_at=updated_at,
        )
