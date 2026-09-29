"""沙箱工作区路径模型与校验。"""

import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from app.sandbox.errors import SandboxPathError

SANDBOX_DATA_ROOT = "/data"
SANDBOX_STAGING_ROOT = "/data/.dataagent-staging"
_PATH_MAX_BYTES = 4096
_PATH_COMPONENT_MAX_BYTES = 255


@dataclass(frozen=True, slots=True)
class SandboxReadonlyMount:
    """一个暴露给沙箱容器的宿主机只读目录。"""

    source: Path
    target: PurePosixPath

    def __post_init__(self) -> None:
        """解析代码配置的宿主目录，保持 Docker 挂载源为绝对路径。"""
        object.__setattr__(self, "source", self.source.resolve(strict=True))


def conversation_workspace_path(conversation_id: UUID) -> str:
    """生成 Conversation 在容器中的完整工作目录。"""
    return posixpath.join(SANDBOX_DATA_ROOT, str(conversation_id))


def conversation_relative_path(path: str, conversation_id: UUID) -> str:
    """将 Conversation 内的沙箱绝对路径转换为公开相对路径。"""
    normalized = normalize_sandbox_path(path)
    root = PurePosixPath(conversation_workspace_path(conversation_id))
    candidate = PurePosixPath(normalized)
    if not candidate.is_relative_to(root):
        raise SandboxPathError(detail=path)
    relative = candidate.relative_to(root).as_posix()
    if relative == "." or any(
        part.startswith(".") for part in PurePosixPath(relative).parts
    ):
        raise SandboxPathError(detail=path)
    return relative


def normalize_attachment_path(path: str) -> str:
    """校验并规范化会话内的附件相对路径。"""
    encoded_path = path.encode("utf-8", errors="surrogatepass")
    if (
        not path
        or path.startswith(("/", "~"))
        or "\\" in path
        or any(character == "\x7f" or ord(character) < 32 for character in path)
        or len(encoded_path) > _PATH_MAX_BYTES
    ):
        raise SandboxPathError(detail=path)
    parts = PurePosixPath(path).parts
    if not parts or any(
        part in {"", ".", ".."}
        or len(part.encode("utf-8", errors="surrogatepass")) > _PATH_COMPONENT_MAX_BYTES
        for part in parts
    ):
        raise SandboxPathError(detail=path)
    return PurePosixPath(*parts).as_posix()


def normalize_sandbox_path(path: str) -> str:
    """按容器 Shell 语义规范化相对路径或绝对路径。"""
    encoded_path = path.encode("utf-8", errors="surrogatepass")
    if (
        not path
        or path.startswith("~")
        or "\\" in path
        or any(character == "\x7f" or ord(character) < 32 for character in path)
        or len(encoded_path) > _PATH_MAX_BYTES
    ):
        raise SandboxPathError(detail=path)
    normalized = posixpath.normpath(path)
    parts = PurePosixPath(normalized).parts
    if any(
        len(part.encode("utf-8", errors="surrogatepass")) > _PATH_COMPONENT_MAX_BYTES
        for part in parts
    ):
        raise SandboxPathError(detail=path)
    return normalized


def resolve_sandbox_path(path: str, working_directory: str) -> str:
    """像 Shell 一样以当前工作目录解析相对路径，并保留绝对路径。"""
    normalized = normalize_sandbox_path(path)
    if normalized.startswith("/"):
        return normalized
    return posixpath.normpath(posixpath.join(working_directory, normalized))
