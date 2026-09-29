"""专业 Agent 的能力定义与实例创建。"""

from collections.abc import Mapping
from pathlib import PurePosixPath

from deepagents.middleware.subagents import CompiledSubAgent
from langchain_core.language_models import BaseChatModel

from app.assistant.agents.agent import create_agent
from app.assistant.agents.filesystem import (
    agent_skills_mount_path,
    build_specialist_filesystem,
)
from app.assistant.agents.resources import ASSISTANT_RESOURCES_DIR, SYSTEM_PROMPTS
from app.assistant.agents.tools.execute_sql import create_execute_sql_tool
from app.assistant.agents.tools.semantic_recall import create_semantic_recall_tool
from app.metadata.services.recall_handler import SemanticRecallHandler
from app.query.services.execution_handler import QueryExecutionHandler
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.paths import SandboxReadonlyMount
from app.shared.contracts.analysis import (
    AgentType,
)


def build_specialists(
    models: Mapping[AgentType, BaseChatModel],
    backend: DockerSandboxBackend,
    recall: SemanticRecallHandler,
    query: QueryExecutionHandler,
) -> list[CompiledSubAgent]:
    """构造无 Checkpoint 的专业图，共用当前会话工作区。"""
    analyst_skills = agent_skills_mount_path("analyst")
    return [
        CompiledSubAgent(
            name="explorer",
            description="检索元数据、执行 SQL，取得可信数据并返回文件路径。",
            runnable=create_agent(
                name="explorer",
                system_prompt=SYSTEM_PROMPTS["explorer"],
                model=models["explorer"],
                tools=[
                    create_semantic_recall_tool(recall),
                    create_execute_sql_tool(query),
                ],
                sandbox=backend,
                filesystem=build_specialist_filesystem(backend),
                checkpointer=False,
            ),
        ),
        CompiledSubAgent(
            name="analyst",
            description="基于数据文件进行分析、计算和可视化。",
            runnable=create_agent(
                name="analyst",
                system_prompt=SYSTEM_PROMPTS["analyst"],
                model=models["analyst"],
                tools=[],
                sandbox=backend,
                filesystem=build_specialist_filesystem(
                    backend,
                    skill_mount=SandboxReadonlyMount(
                        source=ASSISTANT_RESOURCES_DIR / "analyst" / "skills",
                        target=PurePosixPath(analyst_skills),
                    ),
                ),
                skills=[analyst_skills],
                checkpointer=False,
            ),
        ),
        CompiledSubAgent(
            name="reviewer",
            description="检查分析口径、计算过程与交付物。",
            runnable=create_agent(
                name="reviewer",
                system_prompt=SYSTEM_PROMPTS["reviewer"],
                model=models["reviewer"],
                tools=[],
                sandbox=backend,
                filesystem=build_specialist_filesystem(backend),
                checkpointer=False,
            ),
        ),
    ]
