from asyncio import Lock
from collections.abc import AsyncIterator
from typing import Any

from app.core.agent import build_agent
from app.schemas import chat as chat_schema
from langgraph.graph.state import CompiledStateGraph

_agent_cache: dict[tuple[int, int], Any] = {}
_agent_locks: dict[tuple[int, int], Lock] = {}
_cache_lock = Lock()


def _message_item_to_dict(message: chat_schema.MessageItem) -> dict[str, Any]:
    content_parts: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []

    for part in message.parts:
        if isinstance(part, (chat_schema.TextContent, chat_schema.ImageContent)):
            content_parts.append(part.model_dump())
        elif isinstance(part, chat_schema.ToolCallPart):
            tool_calls.append(
                {
                    "id": part.tool_call_id,
                    "name": part.name,
                    "args": part.args,
                    "type": "tool_call",
                }
            )

    payload: dict[str, Any] = {"role": message.role}
    if message.role == "tool":
        tool_result = next(
            part
            for part in message.parts
            if isinstance(part, chat_schema.ToolResultPart)
        )
        payload["content"] = tool_result.content
        payload["tool_call_id"] = tool_result.tool_call_id
        payload["name"] = tool_result.name
        return payload

    payload["content"] = content_parts
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


async def _get_agent(user_id: int, conversation_id: int) -> CompiledStateGraph:
    key = (user_id, conversation_id)

    async with _cache_lock:
        # 用全局锁保护缓存字典和锁字典，确保同一个会话只会分配到同一把会话锁
        agent = _agent_cache.get(key)
        if agent is not None:
            return agent

        # 会话锁只串行当前会话的 agent 初始化，不阻塞其他会话
        agent_lock = _agent_locks.setdefault(key, Lock())

    async with agent_lock:
        # 二次检查，避免等待会话锁期间被其他协程重复创建
        agent = _agent_cache.get(key)
        if agent is None:
            agent = await build_agent(
                user_id=user_id, conversation_id=str(conversation_id)
            )
            _agent_cache[key] = agent

    return agent


async def astream(
    user_id: int,
    conversation_id: int,
    message: chat_schema.MessageItem,
) -> AsyncIterator[chat_schema.WebSocketMessageResponse]:
    """基于当前会话消息流式生成 Agent 响应"""
    # 获取 Agent 实例
    key = (user_id, conversation_id)
    agent = await _get_agent(user_id=user_id, conversation_id=conversation_id)
    agent_lock = _agent_locks[key]

    # 转换消息格式
    user_message = _message_item_to_dict(message)

    # TODO 添加消息上下文(只输入每轮用户消息和最终模型回复，不输入工具调用和工具结果)

    # 生成 Agent 响应
    async with agent_lock:
        async for chunk in agent.astream(input={"messages": [user_message]}):
            message_item = chat_schema.MessageItem.from_agent_chunk(chunk)
            if message_item is not None:
                yield chat_schema.WebSocketMessageResponse(message=message_item)

    # TODO 消息入库(存储所有类型的消息，包括用户消息、模型消息、工具调用和工具结果)
