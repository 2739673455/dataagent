from functools import partial

from deepagents.middleware.subagents import CompiledSubAgent
from langchain_core.runnables import RunnableLambda

from app.assistant.agents.middleware.task_activity import stream_task

"""专业 Agent 的能力定义与实例创建。"""


from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.assistant.agents.agent import create_agent
from app.assistant.agents.filesystem import (
    agent_skills_mount_path,
    build_specialist_filesystem,
)
from app.assistant.resources import ASSISTANT_RESOURCES_DIR, SYSTEM_PROMPTS
from app.sandbox.backend import DockerSandboxBackend
from app.shared.contracts.analysis import (
    AgentType,
)


@dataclass(frozen=True, slots=True)
class SpecialistDefinition:
    """一种专业 Agent 的提示词、技能目录和专属能力。"""

    system_prompt: str
    skill_directory: Path
    tools: tuple[BaseTool, ...] = ()
    skills: tuple[str, ...] = ()


def build_specialist_definitions(
    explorer_tools: Iterable[BaseTool],
) -> dict[AgentType, SpecialistDefinition]:
    """构造专业 Agent 定义，并将数据访问能力限定给 Explorer。"""
    return {
        "explorer": SpecialistDefinition(
            system_prompt=SYSTEM_PROMPTS["explorer"],
            skill_directory=ASSISTANT_RESOURCES_DIR / "explorer" / "skills",
            tools=tuple(explorer_tools),
        ),
        "analyst": SpecialistDefinition(
            system_prompt=SYSTEM_PROMPTS["analyst"],
            skill_directory=ASSISTANT_RESOURCES_DIR / "analyst" / "skills",
            skills=(agent_skills_mount_path("analyst"),),
        ),
        "reviewer": SpecialistDefinition(
            system_prompt=SYSTEM_PROMPTS["reviewer"],
            skill_directory=ASSISTANT_RESOURCES_DIR / "reviewer" / "skills",
        ),
    }


def build_specialists(
    definitions: Mapping[AgentType, SpecialistDefinition],
    models: Mapping[AgentType, BaseChatModel],
    backend: DockerSandboxBackend,
) -> list[CompiledSubAgent]:
    """构造无 Checkpoint 的专业图，共用当前会话工作区。"""
    descriptions = {
        "explorer": "检索元数据、执行 SQL，取得可信数据并返回文件路径。",
        "analyst": "基于数据文件进行分析、计算和可视化。",
        "reviewer": "检查分析口径、计算过程与交付物。",
    }
    agents = []
    for kind, definition in definitions.items():
        _, filesystem = build_specialist_filesystem(
            backend, definition.skill_directory, definition.skills
        )
        graph = create_agent(
            name=kind,
            system_prompt=definition.system_prompt,
            model=models[kind],
            tools=definition.tools,
            sandbox=backend,
            filesystem=filesystem,
            skills=definition.skills,
            checkpointer=False,
        )
        agents.append(
            CompiledSubAgent(
                name=kind,
                description=descriptions[kind],
                runnable=RunnableLambda(partial(stream_task, graph)),
            )
        )
    return agents
