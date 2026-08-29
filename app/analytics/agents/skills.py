"""专业 Agent 内置技能挂载"""

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from deepagents import FilesystemMiddleware
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.filesystem import FilesystemPermission

from app.sandbox.paths import SandboxReadonlyMount
from app.shared.contracts.analysis import AGENT_TYPES, AgentType

AGENT_SKILLS_MOUNT_ROOT = PurePosixPath("/skills")


def agent_skills_mount_path(agent_type: AgentType) -> str:
    """返回指定 Agent 的技能挂载路径"""
    return f"{AGENT_SKILLS_MOUNT_ROOT}/{agent_type}/"


def packaged_agent_skill_mounts() -> tuple[SandboxReadonlyMount, ...]:
    """收集随应用发布且需要暴露给沙箱的 Agent 技能目录"""
    agents_directory = Path(__file__).parent
    return tuple(
        SandboxReadonlyMount(
            source=skill_directory,
            target=AGENT_SKILLS_MOUNT_ROOT / agent_type,
        )
        for agent_type in AGENT_TYPES
        if (skill_directory := agents_directory / agent_type / "skills").is_dir()
    )


def mount_agent_skills(
    backend: BackendProtocol,
    skill_directory: Path,
    skills: Sequence[str],
) -> tuple[BackendProtocol, FilesystemMiddleware]:
    """将当前 Agent 的内置技能只读挂载到会话 Backend"""
    permissions: list[FilesystemPermission] = []
    resolved_backend = backend
    if skills:
        if len(skills) != 1:
            raise ValueError("每个 Agent 只能配置一个技能根目录")
        if not skill_directory.is_dir():
            raise ValueError(f"Agent 技能目录不存在: {skill_directory}")
        mount_path = skills[0]
        if not mount_path.startswith("/") or not mount_path.endswith("/"):
            raise ValueError(f"Agent 技能挂载路径无效: {mount_path}")
        resolved_backend = CompositeBackend(
            default=backend,
            routes={
                mount_path: FilesystemBackend(
                    root_dir=skill_directory,
                    virtual_mode=True,
                )
            },
        )
        permissions.append(
            FilesystemPermission(
                operations=["write"],
                paths=[f"{mount_path}**"],
                mode="deny",
            )
        )
    filesystem = FilesystemMiddleware(
        backend=resolved_backend,
        tools=[
            "ls",
            "read_file",
            "write_file",
            "edit_file",
            "delete",
            "glob",
            "grep",
        ],
        _permissions=permissions,
    )
    return resolved_backend, filesystem
