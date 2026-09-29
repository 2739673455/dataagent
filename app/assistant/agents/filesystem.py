"""Agent 文件系统装配。"""

from pathlib import PurePosixPath

from deepagents import FilesystemMiddleware
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.filesystem import FilesystemPermission, FsToolName

from app.assistant.agents.resources import ASSISTANT_RESOURCES_DIR
from app.sandbox import DockerSandboxBackend, SandboxReadonlyMount
from app.shared.contracts.analysis import AGENT_TYPES, AgentType

_AGENT_SKILLS_MOUNT_ROOT = "/skills"


def agent_skills_mount_path(agent_type: AgentType) -> str:
    """返回指定 Agent 的只读技能挂载路径。"""
    return f"{_AGENT_SKILLS_MOUNT_ROOT}/{agent_type}/"


def packaged_skill_readonly_mounts() -> tuple[SandboxReadonlyMount, ...]:
    """收集随应用发布且需要暴露给沙箱的技能目录。"""
    return tuple(
        SandboxReadonlyMount(
            source=skill_directory,
            target=PurePosixPath(agent_skills_mount_path(agent_type)),
        )
        for agent_type in AGENT_TYPES
        if (skill_directory := ASSISTANT_RESOURCES_DIR / agent_type / "skills").is_dir()
    )


def build_agent_filesystem(
    backend: DockerSandboxBackend,
    *,
    tools: list[FsToolName] | None = None,
    skill_mount: SandboxReadonlyMount | None = None,
) -> FilesystemMiddleware:
    """统一装配会话文件工具、交付规则和可选只读技能目录。"""
    workspace_dir = backend.workspace_dir
    permissions: list[FilesystemPermission] = []
    routes: dict[str, BackendProtocol] = {}
    if skill_mount is not None:
        mount_path = f"{skill_mount.target.as_posix().rstrip('/')}/"
        routes[mount_path] = FilesystemBackend(
            root_dir=skill_mount.source,
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
    return FilesystemMiddleware(
        backend=resolved_backend,
        system_prompt=f"""## 沙箱路径

当前会话工作目录是 `{workspace_dir}`。

- 文件工具和 `shell` 使用同一套容器路径：相对路径从当前会话工作目录解析，绝对路径直接使用。
- 所有 Agent 共用当前会话目录；写入或编辑文件时只能修改该目录，避免覆盖其他任务的同名文件。
- 跨 Agent 传递文件使用完整绝对路径。
- 内置技能位于只读 `/skills/...`。

## 文件交付

- 交付文件必须已写入当前会话目录并确认存在，使用工具返回或经 shell 确认的完整绝对路径。
- 每个交付标记独占一行：`[[DATAAGENT_ARTIFACT:<absolute_path>]]`。不要放在列表、表格或代码块中，文件用途在正文另行说明。
""",
        tools=tools if tools is not None else ["read_file", "write_file", "edit_file"],
        _permissions=permissions,
    )
