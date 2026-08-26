"""沙箱工作区路径模型与校验"""

from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

from app.sandbox.exceptions import SandboxPathError

SANDBOX_WORKSPACE_ROOT = "/workspace/conversations"
SANDBOX_STAGING_ROOT = "/workspace/.dataagent-staging"
_PATH_MAX_BYTES = 4096
_PATH_COMPONENT_MAX_BYTES = 255


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
        return (
            f"analyses/{self.analysis_id}/sessions/{self.agent_type}/{self.session_id}"
        )

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
    """校验用户可变附件路径并隔离系统分析产物目录"""
    normalized_path = normalize_attachment_path(path)
    if PurePosixPath(normalized_path).parts[0] == "analyses":
        raise SandboxPathError(path)
    return normalized_path
