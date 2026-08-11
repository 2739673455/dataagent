import asyncio
from collections import OrderedDict
from pathlib import Path
from uuid import UUID

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.agent.mcp import get_mcp_tools
from app.agent.tools import return_file, search_semantics
from app.clients.docker_sandbox_manager import (
    DockerSandboxBackend,
    docker_sandbox_manager,
)
from app.clients.langgraph_postgres_manager import (
    LangGraphPostgresManager,
    langgraph_postgres_manager,
)
from app.conf import app_config

# 路径常量
ROOT_DIR = Path(__file__).parents[2]  # 项目根目录
DEEPAGENTS_ROOT = ROOT_DIR / ".deepagents"
SKILLS_DIR = DEEPAGENTS_ROOT / "skills"

type AgentKey = tuple[int, UUID]

_DEFAULT_MAX_CACHED_AGENTS = 128


def get_thread_id(user_id: int, conversation_id: UUID) -> str:
    """构造全局唯一的 LangGraph 会话线程 ID"""
    return f"user_{user_id}:conversation_{conversation_id}"


def get_agent_config(user_id: int, conversation_id: UUID) -> RunnableConfig:
    """创建包含持久化线程和沙盒上下文的 Agent 运行配置"""
    return RunnableConfig(
        configurable={
            "thread_id": get_thread_id(user_id, conversation_id),
            "user_id": user_id,
            "conversation_id": str(conversation_id),
            "workspace_dir": "/",
        }
    )


def _build_backend(sandbox_backend: DockerSandboxBackend) -> CompositeBackend:
    """创建会话沙盒与技能目录的组合后端"""
    # 技能文件系统后端
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skills_backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)

    # CompositeBackend 将多个后端合并为一个统一视图，对 Agent 透明：
    # - default: 当前用户沙盒中的会话目录，处理除 /skills/ 外的所有路径
    # - routes["/skills/"]: 命中此前缀时，剥离前缀后将剩余路径转发到 skills_backend
    #   例如 Agent 请求 /skills/insight/SKILL.md → skills_backend 收到 insight/SKILL.md
    return CompositeBackend(
        default=sandbox_backend, routes={"/skills/": skills_backend}
    )


class AgentManager:
    """管理共享 Agent 资源和会话级 Agent 实例"""

    def __init__(
        self,
        persistence_manager: LangGraphPostgresManager,
        max_cached_agents: int = _DEFAULT_MAX_CACHED_AGENTS,
    ) -> None:
        """初始化 Agent 管理器"""
        if max_cached_agents <= 0:
            raise ValueError("max_cached_agents must be positive")
        self._persistence_manager = persistence_manager
        self._max_cached_agents = max_cached_agents
        self._agents: OrderedDict[AgentKey, CompiledStateGraph] = OrderedDict()
        self._build_tasks: dict[AgentKey, asyncio.Task[CompiledStateGraph]] = {}
        self._state_lock = asyncio.Lock()
        self._model: BaseChatModel | None = None
        self._tools: list[BaseTool] | None = None

    async def init(self) -> None:
        """初始化所有 Agent 共享的模型和工具"""
        async with self._state_lock:
            if self._model is not None and self._tools is not None:
                return

            model_cfg = app_config.cfg.lm_config.models[app_config.cfg.lm_config.active]
            model = init_chat_model(
                model_provider=model_cfg.model_provider,
                model=model_cfg.model,
                base_url=model_cfg.base_url,
                api_key=model_cfg.api_key,
                profile=model_cfg.profile,
                request_timeout=30,
                max_retries=2,
                **model_cfg.params,
            )
            tools: list[BaseTool] = [
                search_semantics,
                return_file,
                *await get_mcp_tools(),
            ]
            self._model = model
            self._tools = tools

    def _build_agent(
        self,
        sandbox_backend: DockerSandboxBackend,
    ) -> CompiledStateGraph:
        """创建会话级 Agent 实例"""
        if self._model is None or self._tools is None:
            raise RuntimeError("Agent manager is not initialized")
        return create_deep_agent(
            model=self._model,
            tools=self._tools,
            backend=_build_backend(sandbox_backend),
            skills=["/skills/"],
            checkpointer=self._persistence_manager.get_checkpointer(),
            store=self._persistence_manager.get_store(),
        )

    async def _build_and_cache_agent(
        self,
        agent_key: AgentKey,
        user_id: int,
        conversation_id: UUID,
    ) -> CompiledStateGraph:
        """构建 Agent 并写入会话缓存"""
        current_task = asyncio.current_task()
        try:
            sandbox_backend = await docker_sandbox_manager.get_backend(
                user_id,
                conversation_id,
            )
            agent = self._build_agent(sandbox_backend)
        except (Exception, asyncio.CancelledError):
            async with self._state_lock:
                if self._build_tasks.get(agent_key) is current_task:
                    self._build_tasks.pop(agent_key, None)
            raise

        async with self._state_lock:
            if self._build_tasks.get(agent_key) is current_task:
                self._build_tasks.pop(agent_key, None)
                self._agents[agent_key] = agent
                self._agents.move_to_end(agent_key)
                while len(self._agents) > self._max_cached_agents:
                    self._agents.popitem(last=False)
        return agent

    async def get_agent(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> CompiledStateGraph:
        """获取会话级 Agent 实例，不存在时按需创建"""
        await self.init()
        agent_key = (user_id, conversation_id)
        async with self._state_lock:
            if agent := self._agents.get(agent_key):
                self._agents.move_to_end(agent_key)
                return agent
            build_task = self._build_tasks.get(agent_key)
            if build_task is None:
                build_task = asyncio.create_task(
                    self._build_and_cache_agent(
                        agent_key,
                        user_id,
                        conversation_id,
                    )
                )
                self._build_tasks[agent_key] = build_task
        return await asyncio.shield(build_task)

    async def reset(self) -> None:
        """清空缓存并按最新配置重新初始化共享资源"""
        await self.close()
        await self.init()

    async def delete_agent(self, user_id: int, conversation_id: UUID) -> None:
        """删除会话 Agent 实例及其持久化状态"""
        agent_key = (user_id, conversation_id)
        async with self._state_lock:
            self._agents.pop(agent_key, None)
            build_task = self._build_tasks.pop(agent_key, None)
        if build_task is not None:
            build_task.cancel()
            await asyncio.gather(build_task, return_exceptions=True)
        await self._persistence_manager.delete_thread(
            get_thread_id(user_id, conversation_id)
        )

    async def close(self) -> None:
        """释放 Agent 缓存和未完成的构建任务"""
        async with self._state_lock:
            build_tasks = list(self._build_tasks.values())
            self._build_tasks.clear()
            self._agents.clear()
            self._model = None
            self._tools = None
        for build_task in build_tasks:
            build_task.cancel()
        if build_tasks:
            await asyncio.gather(*build_tasks, return_exceptions=True)


agent_manager = AgentManager(langgraph_postgres_manager)
