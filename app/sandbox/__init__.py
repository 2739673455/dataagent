"""沙箱公共类型与路径操作；执行资源由管理器持有。"""

from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.errors import SandboxFileTooLargeError, SandboxPathError
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.paths import (
    SandboxReadonlyMount,
    SandboxSessionScope,
    conversation_workspace_path,
    normalize_attachment_path,
    normalize_sandbox_path,
    resolve_sandbox_path,
)
from app.sandbox.shell_runner import (
    DockerShellJobRunner,
    SandboxShellJobCancellation,
    SandboxShellJobExecution,
)

__all__ = [
    "DockerSandboxBackend",
    "DockerSandboxManager",
    "DockerShellJobRunner",
    "SandboxFileTooLargeError",
    "SandboxPathError",
    "SandboxReadonlyMount",
    "SandboxSessionScope",
    "SandboxShellJobCancellation",
    "SandboxShellJobExecution",
    "conversation_workspace_path",
    "normalize_attachment_path",
    "normalize_sandbox_path",
    "resolve_sandbox_path",
]
