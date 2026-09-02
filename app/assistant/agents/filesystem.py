"""专业 Agent 文件系统装配。"""

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from deepagents import FilesystemMiddleware
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.filesystem import FilesystemPermission

from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.paths import SandboxReadonlyMount
from app.shared.contracts.analysis import AGENT_TYPES, AgentType

_AGENT_SKILLS_MOUNT_ROOT = "/skills"


def agent_skills_mount_path(agent_type: AgentType) -> str:
    """返回指定 Agent 的只读技能挂载路径。"""
    return f"{_AGENT_SKILLS_MOUNT_ROOT}/{agent_type}/"


def packaged_skill_readonly_mounts() -> tuple[SandboxReadonlyMount, ...]:
    """收集随应用发布且需要暴露给沙箱的技能目录。"""
    agents_directory = Path(__file__).parent
    return tuple(
        SandboxReadonlyMount(
            source=skill_directory,
            target=PurePosixPath(agent_skills_mount_path(agent_type)),
        )
        for agent_type in AGENT_TYPES
        if (skill_directory := agents_directory / agent_type / "skills").is_dir()
    )


def _filesystem_system_prompt(workspace_dir: str) -> str:
    """生成 Specialist 文件工具与 Shell 路径边界说明。"""
    return f"""## 沙箱路径

当前 Session 工作目录是 `{workspace_dir}`。

- 文件工具、`view_image` 和 `shell` 使用同一套容器路径：相对路径从当前 Session 工作目录解析，绝对路径直接使用。
- `write_file` 和 `edit_file` 只能修改当前 Session 工作目录；同一 Conversation 的其他 Session 和上传文件只读。
- `artifacts` 可以使用相对当前 Session 的路径或完整绝对路径；跨 Agent 传递前会统一解析为绝对路径。
- 内置技能位于只读 `/skills/...`。
"""


def build_specialist_filesystem(
    backend: DockerSandboxBackend,
    skill_directory: Path,
    skills: Sequence[str],
) -> tuple[BackendProtocol, FilesystemMiddleware]:
    """创建带只读技能目录的 Specialist 文件系统。"""
    workspace_dir = backend.workspace_dir
    permissions: list[FilesystemPermission] = []
    routes: dict[str, BackendProtocol] = {}
    if skills:
        if len(skills) != 1:
            raise ValueError("每个 Agent 只能配置一个技能根目录")
        if not skill_directory.is_dir():
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
        system_prompt=_filesystem_system_prompt(workspace_dir),
        tools=[
            "read_file",
            "write_file",
            "edit_file",
        ],
        _permissions=permissions,
    )
    return resolved_backend, filesystem
