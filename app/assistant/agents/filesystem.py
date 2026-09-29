"""专业 Agent 文件系统装配。"""

from pathlib import PurePosixPath

from deepagents import FilesystemMiddleware
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.filesystem import FilesystemPermission

from app.assistant.agents.resources import ASSISTANT_RESOURCES_DIR
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.paths import SandboxReadonlyMount
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


def build_specialist_filesystem(
    backend: DockerSandboxBackend,
    *,
    skill_mount: SandboxReadonlyMount | None = None,
) -> FilesystemMiddleware:
    """创建带只读技能目录的 Specialist 文件系统。"""
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
- 所有 Agent 共用当前会话目录；`write_file` 和 `edit_file` 只能修改该目录，避免覆盖其他任务的同名文件。
- 跨 Agent 传递文件使用完整绝对路径。
- 内置技能位于只读 `/skills/...`。

## 任务结果与文件交付

- 用文本说明结论、证据、限制和未完成事项，不需要输出固定 JSON 结构。
- 数据或上游产物不足时，说明具体问题、所需输入和目标 Agent，由 Planner 调度新任务；不要假定本次执行能等待上游修复后自动恢复。
- 交付文件必须已写入当前会话目录并确认存在，使用工具返回或经 shell 确认的完整绝对路径。
- 每个交付标记独占一行：`[[DATAAGENT_ARTIFACT:<absolute_path>]]`。不要放在列表、表格或代码块中，文件用途在正文另行说明。
""",
        tools=[
            "read_file",
            "write_file",
            "edit_file",
        ],
        _permissions=permissions,
    )
