"""沙箱公共入口：资源管理、会话执行、只读挂载与产物路径。"""

from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.errors import SandboxFileTooLargeError, SandboxPathError
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.paths import SandboxReadonlyMount, conversation_relative_path
from app.sandbox.shell_runner import ShellResult

__all__ = [
    "DockerSandboxBackend",
    "DockerSandboxManager",
    "SandboxFileTooLargeError",
    "SandboxPathError",
    "SandboxReadonlyMount",
    "ShellResult",
    "conversation_relative_path",
]
