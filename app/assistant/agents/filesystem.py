"""Agent 共用文件系统装配与路径交付协议。"""

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from deepagents import FilesystemMiddleware
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.filesystem import FilesystemPermission, FsToolName

from app.assistant.resource_loader import SKILLS_DIRECTORY, load_prompt
from app.sandbox.application import DockerSandboxBackend
from app.sandbox.contracts import SandboxReadonlyMount
from app.shared.contracts.analysis import AgentType

_AGENT_SKILLS_MOUNT_ROOT = "/skills"


def agent_skills_mount_path(agent_type: AgentType) -> str:
    """返回指定 Agent 的只读技能挂载路径。"""
    return f"{_AGENT_SKILLS_MOUNT_ROOT}/{agent_type}/"


def packaged_skill_readonly_mounts() -> tuple[SandboxReadonlyMount, ...]:
    """收集随应用发布且需要暴露给沙箱的技能目录。"""
    return (
        SandboxReadonlyMount(
            source=SKILLS_DIRECTORY,
            target=PurePosixPath(agent_skills_mount_path("analyst")),
        ),
    )


def build_agent_filesystem(
    backend: DockerSandboxBackend,
    *,
    tools: Sequence[FsToolName],
    skill_directory: Path | None = None,
    skills: Sequence[str] = (),
) -> tuple[BackendProtocol, FilesystemMiddleware]:
    """按角色工具范围创建文件系统，并挂载可选的只读技能。"""
    workspace_dir = backend.workspace_dir
    permissions: list[FilesystemPermission] = []
    routes: dict[str, BackendProtocol] = {}
    if skills:
        if len(skills) != 1:
            raise ValueError("每个 Agent 只能配置一个技能根目录")
        if skill_directory is None or not skill_directory.is_dir():
            raise ValueError(f"Agent 技能目录不存在: {skill_directory}")
        mount_path = skills[0]
        if not mount_path.startswith("/") or not mount_path.endswith("/"):
            raise ValueError(f"Agent 技能挂载路径无效: {mount_path}")
        routes[mount_path] = FilesystemBackend(
            root_dir=skill_directory,
            virtual_mode=True,
        )
        permissions.append(
            FilesystemPermission(
                operations=["write"],
                paths=[f"{mount_path}**"],
                mode="deny",
            )
        )
    resolved_backend = CompositeBackend(
        default=backend,
        routes=routes,
        artifacts_root=workspace_dir,
    )
    filesystem = FilesystemMiddleware(
        backend=resolved_backend,
        system_prompt=load_prompt("filesystem").format(
            workspace_dir=workspace_dir,
        ),
        tools=list(tools),
        _permissions=permissions,
    )
    return resolved_backend, filesystem
