"""沙箱路径、挂载及命令执行的公开数据契约。"""

import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID

SANDBOX_DATA_ROOT = "/data"
SANDBOX_STAGING_ROOT = "/data/.dataagent-staging"
USER_ATTACHMENT_ROOT = "uploads"


@dataclass(frozen=True, slots=True)
class SandboxReadonlyMount:
    """一个暴露给沙箱容器的宿主机只读目录。"""

    source: Path
    target: PurePosixPath

    def __post_init__(self) -> None:
        """规范化源目录并校验容器目标路径。"""
        source = self.source.resolve(strict=True)
        if not source.is_dir():
            raise ValueError(f"沙箱只读挂载源不是目录: {source}")
        target = self.target
        if (
            not target.is_absolute()
            or target == PurePosixPath("/")
            or target == PurePosixPath(SANDBOX_DATA_ROOT)
            or target.is_relative_to(PurePosixPath(SANDBOX_DATA_ROOT))
            or target == PurePosixPath("/tmp")
            or target.is_relative_to(PurePosixPath("/tmp"))
        ):
            raise ValueError(f"沙箱只读挂载目标路径无效: {target}")
        object.__setattr__(self, "source", source)


@dataclass(frozen=True, slots=True)
class SandboxSessionScope:
    """定位一个专业 Agent Session 工作区。"""

    analysis_id: str
    agent_type: str
    session_id: str

    def __post_init__(self) -> None:
        """校验 Agent Session 路径字段可安全用于工作区。"""
        for field_name, value in (
            ("analysis_id", self.analysis_id),
            ("agent_type", self.agent_type),
            ("session_id", self.session_id),
        ):
            if (
                not value
                or len(value.encode("utf-8")) > 64
                or not value[0].isalnum()
                or any(
                    not character.islower()
                    and not character.isdigit()
                    and character not in {"-", "_"}
                    for character in value
                )
            ):
                raise ValueError(f"沙箱 Session 字段无效: {field_name}")

    @property
    def relative_workspace(self) -> str:
        """生成 conversation 根目录下的 Session 路径。"""
        return f"sessions/{self.analysis_id}/{self.agent_type}/{self.session_id}"

    def registry_key(self, conversation_id: UUID) -> str:
        """生成 UID 注册表中的稳定 Session 键。"""
        return f"{conversation_id}/{self.relative_workspace}"

    def workspace_path(self, conversation_id: UUID) -> str:
        """生成 Session 在容器中的完整工作目录。"""
        return posixpath.join(
            posixpath.join(SANDBOX_DATA_ROOT, str(conversation_id)),
            self.relative_workspace,
        )


@dataclass(frozen=True, slots=True)
class SandboxArtifact:
    """产物的容器绝对路径与会话下载相对路径。"""

    path: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class SandboxShellJobExecution:
    """Sandbox Shell Job 的最终执行信息。"""

    status: Literal["completed", "failed", "interrupted"]
    exit_code: int | None = None
    output: str | None = None
    output_inline_truncated: bool = False
    output_truncated: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxShellJobCancellation:
    """Sandbox 进程组取消结果。"""

    ready: bool
    signal_sent: bool
    exited: bool
