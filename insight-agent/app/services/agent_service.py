from asyncio import Lock
from typing import Any

from app.core.agent import build_agent
from langgraph.graph.state import CompiledStateGraph

_agent_cache: dict[tuple[int, int], Any] = {}
_agent_locks: dict[tuple[int, int], Lock] = {}
_cache_lock = Lock()


async def _get_agent(user_id: int, conversation_id: int) -> CompiledStateGraph:
    """按用户和会话获取复用的 Agent 实例，不存在时按需创建"""
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
            agent = await build_agent(user_id, conversation_id)
            _agent_cache[key] = agent

    return agent


async def cleanup_agent(user_id: int, conversation_id: int) -> None:
    """清理指定会话的 Agent 实例和锁"""
    key = (user_id, conversation_id)

    async with _cache_lock:
        _agent_cache.pop(key, None)
        _agent_locks.pop(key, None)


async def astream(user_id: int, conversation_id: int, messages: list):
    """基于当前会话消息流式生成 Agent 响应"""
    # 获取 Agent 实例
    key = (user_id, conversation_id)
    agent = await _get_agent(user_id, conversation_id)
    agent_lock = _agent_locks[key]

    # 生成 Agent 响应
    async with agent_lock:
        async for chunk in agent.astream(input={"messages": messages}):
            yield chunk
