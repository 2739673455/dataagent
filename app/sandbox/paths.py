"""沙箱工作区路径模型与校验。"""

import posixpath
from pathlib import PurePosixPath
from uuid import UUID

from app.sandbox.contracts import (
    SANDBOX_DATA_ROOT,
    USER_ATTACHMENT_ROOT,
    SandboxArtifact,
    SandboxSessionScope,
)
from app.sandbox.errors import SandboxPathError

_CONVERSATION_FILE_ROOTS = frozenset({"sessions", USER_ATTACHMENT_ROOT})
_PATH_MAX_BYTES = 4096
_PATH_COMPONENT_MAX_BYTES = 255


def conversation_workspace_path(conversation_id: UUID) -> str:
    """生成 Conversation 在容器中的完整工作目录。"""
    return posixpath.join(SANDBOX_DATA_ROOT, str(conversation_id))


def resolve_sandbox_path(
    path: str,
    working_directory: str,
    *,
    allowed_root: str | None = None,
) -> str:
    """以工作目录解析容器绝对路径，并按需限制访问范围。"""
    encoded_path = path.encode("utf-8", errors="surrogatepass")
    if (
        not path
        or path.startswith("~")
        or "\\" in path
        or any(character == "\x7f" or ord(character) < 32 for character in path)
        or len(encoded_path) > _PATH_MAX_BYTES
    ):
        raise SandboxPathError(path)
    normalized = posixpath.normpath(posixpath.join(working_directory, path))
    candidate = PurePosixPath(normalized)
    if (
        not candidate.is_absolute()
        or len(normalized.encode("utf-8", errors="surrogatepass")) > _PATH_MAX_BYTES
        or any(
            len(part.encode("utf-8", errors="surrogatepass"))
            > _PATH_COMPONENT_MAX_BYTES
            for part in candidate.parts
        )
    ):
        raise SandboxPathError(path)
    if allowed_root is not None and not candidate.is_relative_to(
        PurePosixPath(posixpath.normpath(allowed_root))
    ):
        raise SandboxPathError(path)
    return normalized


def resolve_attachment_path(
    path: str,
    conversation_id: UUID,
    *,
    writable: bool = False,
) -> SandboxArtifact:
    """解析会话附件；上传和删除限制在 uploads 目录。"""
    root = conversation_workspace_path(conversation_id)
    allowed_root = root
    working_directory = root
    if writable:
        allowed_root = posixpath.join(root, USER_ATTACHMENT_ROOT)
        parts = PurePosixPath(path).parts
        working_directory = (
            root if parts and parts[0] == USER_ATTACHMENT_ROOT else allowed_root
        )
    resolved = resolve_sandbox_path(
        path,
        working_directory,
        allowed_root=allowed_root,
    )
    if resolved == allowed_root:
        raise SandboxPathError(path)
    relative = PurePosixPath(resolved).relative_to(root)
    return SandboxArtifact(resolved, relative.as_posix())


def resolve_artifact_path(
    path: str,
    conversation_id: UUID,
    session_scope: SandboxSessionScope | None = None,
) -> SandboxArtifact:
    """解析公开产物，并检查会话或指定 Session 的目录范围。"""
    root = PurePosixPath(conversation_workspace_path(conversation_id))
    working_directory = (
        session_scope.workspace_path(conversation_id)
        if session_scope is not None
        else root.as_posix()
    )
    candidate = PurePosixPath(
        resolve_sandbox_path(
            path,
            working_directory,
            allowed_root=working_directory,
        )
    )
    if candidate == PurePosixPath(working_directory):
        raise SandboxPathError(path)
    relative = candidate.relative_to(root)
    if (
        not relative.parts
        or relative.parts[0] not in _CONVERSATION_FILE_ROOTS
        or any(part.startswith(".") for part in relative.parts)
    ):
        raise SandboxPathError(path)
    return SandboxArtifact(candidate.as_posix(), relative.as_posix())
