"""专业 Agent 内置技能路径。"""

from pathlib import PurePosixPath

from app.shared.contracts.analysis import AgentType

AGENT_SKILLS_MOUNT_ROOT = PurePosixPath("/skills")


def agent_skills_mount_path(agent_type: AgentType) -> str:
    """返回指定 Agent 的技能挂载路径。"""
    return f"{AGENT_SKILLS_MOUNT_ROOT}/{agent_type}/"
