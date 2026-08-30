"""沙箱工作区路径模型与校验"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from app.sandbox.exceptions import SandboxPathError

SANDBOX_DATA_ROOT = "/data"
SANDBOX_STAGING_ROOT = "/data/.dataagent-staging"
USER_ATTACHMENT_ROOT = "uploads"
_PATH_MAX_BYTES = 4096
_PATH_COMPONENT_MAX_BYTES = 255


@dataclass(frozen=True, slots=True)
class SandboxReadonlyMount:
    """一个暴露给沙箱容器的宿主机只读目录"""

    source: Path
    target: PurePosixPath

    def __post_init__(self) -> None:
        """规范化源目录并校验容器目标路径"""
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
    """定位一个专业 Agent Session 工作区"""

    analysis_id: str
    agent_type: str
    session_id: str

    def __post_init__(self) -> None:
        """校验 Agent Session 路径字段可安全用于工作区"""
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
        """生成 conversation 根目录下的 Session 路径"""
        return f"sessions/{self.analysis_id}/{self.agent_type}/{self.session_id}"

    def registry_key(self, conversation_id: UUID) -> str:
        """生成 UID 注册表中的稳定 Session 键"""
        return f"{conversation_id}/{self.relative_workspace}"


def normalize_attachment_path(path: str) -> str:
    """校验并规范化会话内的附件相对路径"""
    encoded_path = path.encode("utf-8", errors="surrogatepass")
    if (
        not path
        or path.startswith(("/", "~"))
        or "\\" in path
        or any(character == "\x7f" or ord(character) < 32 for character in path)
        or len(encoded_path) > _PATH_MAX_BYTES
    ):
        raise SandboxPathError(path)
    parts = PurePosixPath(path).parts
    if not parts or any(
        part in {"", ".", ".."}
        or len(part.encode("utf-8", errors="surrogatepass")) > _PATH_COMPONENT_MAX_BYTES
        for part in parts
    ):
        raise SandboxPathError(path)
    return PurePosixPath(*parts).as_posix()


def normalize_user_attachment_path(path: str) -> str:
    """将用户可变附件路径限制在统一上传目录"""
    normalized_path = normalize_attachment_path(path)
    if PurePosixPath(normalized_path).parts[0] == USER_ATTACHMENT_ROOT:
        return normalized_path
    return f"{USER_ATTACHMENT_ROOT}/{normalized_path}"
