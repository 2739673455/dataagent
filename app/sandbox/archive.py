"""Docker 沙箱持久工作区归档操作"""

import hashlib
import io
import json
import posixpath
import tarfile
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from uuid import UUID

from docker.errors import NotFound
from docker.models.containers import Container

from app.sandbox.exceptions import (
    SandboxFileTooLargeError,
    SandboxPathError,
    SandboxStorageLimitError,
)
from app.sandbox.paths import (
    SANDBOX_STAGING_ROOT,
    SANDBOX_WORKSPACE_ROOT,
    SandboxSessionScope,
)

_SANDBOX_UID_REGISTRY = "/workspace/.dataagent-uids.json"
_UID_REGISTRY_VERSION = 2
_MIN_SANDBOX_UID = 100_000
_MAX_SANDBOX_UID = 2_147_483_646
_ARCHIVE_SPOOL_BYTES = 8 * 1024 * 1024


@dataclass(slots=True)
class _UidRegistry:
    """持久化 conversation 和 Agent Session 的 Linux UID"""

    conversations: dict[str, int]
    sessions: dict[str, int]


class _IteratorReader(io.RawIOBase):
    """将 Docker archive 字节迭代器适配为 tarfile 可读取的流"""

    def __init__(self, chunks: Any) -> None:
        """绑定 Docker archive 返回的字节块迭代器"""
        super().__init__()
        self._chunks = chunks
        self._buffer = bytearray()
        self._finished = False

    def readable(self) -> bool:
        """声明该适配器支持读取"""
        return True

    def readinto(self, target: Any) -> int:
        """将迭代器数据填充到目标缓冲区"""
        if self.closed:
            return 0
        view = memoryview(target).cast("B")
        while len(self._buffer) < len(view) and not self._finished:
            try:
                self._buffer.extend(next(self._chunks))
            except StopIteration:
                self._finished = True
        size = min(len(view), len(self._buffer))
        view[:size] = self._buffer[:size]
        del self._buffer[:size]
        return size

    def close(self) -> None:
        """关闭底层字节迭代器和读取流"""
        close_chunks = getattr(self._chunks, "close", None)
        if callable(close_chunks):
            close_chunks()
        super().close()


class SandboxArchiveStore:
    """管理停止或运行容器中的持久工作区和文件归档"""

    def __init__(self, max_file_bytes: int, max_workspace_bytes: int) -> None:
        """初始化文件和工作区容量限制"""
        self._max_file_bytes = max_file_bytes
        self._max_workspace_bytes = max_workspace_bytes

    @contextmanager
    def open_archive(
        self,
        container: Container,
        path: str,
    ) -> Generator[tarfile.TarFile, None, None]:
        """流式打开容器中的 archive"""
        chunks, _ = container.get_archive(path)
        raw_reader = _IteratorReader(iter(chunks))
        buffered_reader = io.BufferedReader(raw_reader)
        try:
            with tarfile.open(fileobj=buffered_reader, mode="r|*") as archive:
                yield archive
        finally:
            buffered_reader.close()

    def inspect_path(self, container: Container, path: str) -> tarfile.TarInfo | None:
        """读取容器路径对应的首个 archive 条目"""
        try:
            with self.open_archive(container, path) as archive:
                return next(iter(archive), None)
        except NotFound:
            return None

    def read_file(
        self,
        container: Container,
        path: str,
        max_bytes: int,
    ) -> tuple[bytes, tarfile.TarInfo]:
        """从容器读取一个限长普通文件"""
        with self.open_archive(container, path) as archive:
            member = next(iter(archive), None)
            if member is None or not member.isreg():
                raise FileNotFoundError(path)
            if member.size > max_bytes:
                raise SandboxFileTooLargeError(
                    f"文件大小超出限制: {member.size} > {max_bytes}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise FileNotFoundError(path)
            content = extracted.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise SandboxFileTooLargeError(f"文件大小超出限制: > {max_bytes}")
            return content, member

    def put(
        self,
        container: Container,
        base_path: str,
        directories: list[tuple[str, int, int, int]],
        files: list[tuple[str, int, int, int, BinaryIO, int]],
    ) -> None:
        """构造受控 tar 并写入容器"""
        with tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_BYTES) as buffer:
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                for name, owner_uid, owner_gid, mode in directories:
                    info = tarfile.TarInfo(name=name.rstrip("/") + "/")
                    info.type = tarfile.DIRTYPE
                    info.mode = mode
                    info.uid = owner_uid
                    info.gid = owner_gid
                    archive.addfile(info)
                for name, owner_uid, owner_gid, mode, content, size in files:
                    info = tarfile.TarInfo(name=name)
                    info.size = size
                    info.mode = mode
                    info.uid = owner_uid
                    info.gid = owner_gid
                    archive.addfile(info, content)
            buffer.seek(0)
            if not container.put_archive(base_path, buffer):
                raise OSError(f"写入 Docker 归档失败: {base_path}")

    def _write_registry(self, container: Container, registry: _UidRegistry) -> None:
        """将 UID 注册表持久化到用户数据卷"""
        content = json.dumps(
            {
                "version": _UID_REGISTRY_VERSION,
                "conversations": registry.conversations,
                "sessions": registry.sessions,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.put(
            container,
            "/workspace",
            [],
            [
                (
                    PurePosixPath(_SANDBOX_UID_REGISTRY).name,
                    0,
                    0,
                    0o600,
                    io.BytesIO(content),
                    len(content),
                )
            ],
        )

    @staticmethod
    def _validate_session_key(key: str) -> str:
        """校验并规范化 UID 注册表中的 Session 键"""
        parts = PurePosixPath(key).parts
        if len(parts) != 6 or parts[1] != "analyses" or parts[3] != "sessions":
            raise ValueError("沙箱 Session UID 键无效")
        conversation_id = UUID(parts[0])
        return SandboxSessionScope(parts[2], parts[4], parts[5]).registry_key(
            conversation_id
        )

    @staticmethod
    def _validate_registry(registry: _UidRegistry) -> None:
        """校验 conversation 和 Session UID 全局唯一"""
        values = [*registry.conversations.values(), *registry.sessions.values()]
        if len(values) != len(set(values)):
            raise RuntimeError("沙箱 UID 注册表包含重复的 UID")
        if any(uid < _MIN_SANDBOX_UID or uid > _MAX_SANDBOX_UID for uid in values):
            raise RuntimeError("沙箱 UID 注册表包含无效的 UID")

    def _load_registry(self, container: Container) -> _UidRegistry:
        """读取当前格式的 UID 注册表"""
        try:
            content, member = self.read_file(
                container,
                _SANDBOX_UID_REGISTRY,
                4 * 1024 * 1024,
            )
        except (NotFound, FileNotFoundError):
            registry = _UidRegistry(conversations={}, sessions={})
            self._write_registry(container, registry)
            return registry
        if member.uid != 0:
            raise RuntimeError("沙箱 UID 注册表文件拥有者无效")
        payload = json.loads(content)
        if payload.get("version") != _UID_REGISTRY_VERSION:
            raise RuntimeError("不支持的沙箱 UID 注册表版本")
        raw_conversations = payload.get("conversations")
        raw_sessions = payload.get("sessions")
        if not isinstance(raw_conversations, dict) or not isinstance(
            raw_sessions,
            dict,
        ):
            raise TypeError("沙箱 UID 注册表格式无效")
        registry = _UidRegistry(
            conversations={
                str(UUID(key)): int(value) for key, value in raw_conversations.items()
            },
            sessions={
                self._validate_session_key(key): int(value)
                for key, value in raw_sessions.items()
            },
        )
        self._validate_registry(registry)
        return registry

    @staticmethod
    def _allocate_uid(seed: bytes, used_uids: set[int]) -> int:
        """根据稳定种子确定性分配未使用的 Linux UID"""
        uid_range = _MAX_SANDBOX_UID - _MIN_SANDBOX_UID + 1
        for attempt in range(uid_range):
            digest = hashlib.blake2s(seed + attempt.to_bytes(8, "big")).digest()
            candidate = _MIN_SANDBOX_UID + int.from_bytes(digest[:8], "big") % uid_range
            if candidate not in used_uids:
                return candidate
        raise RuntimeError("沙箱 UID 分配范围已耗尽")

    def ensure_workspace(self, container: Container, conversation_id: UUID) -> int:
        """创建会话工作区并返回稳定 UID"""
        self.put(
            container,
            "/workspace",
            [
                (PurePosixPath(SANDBOX_WORKSPACE_ROOT).name, 0, 0, 0o711),
                (PurePosixPath(SANDBOX_STAGING_ROOT).name, 0, 0, 0o700),
            ],
            [],
        )
        registry = self._load_registry(container)
        key = str(conversation_id)
        conversation_uid = registry.conversations.get(key)
        if conversation_uid is None:
            conversation_uid = self._allocate_uid(
                conversation_id.bytes,
                {*registry.conversations.values(), *registry.sessions.values()},
            )
            registry.conversations[key] = conversation_uid
            self._write_registry(container, registry)

        target_path = f"{SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        existing = self.inspect_path(container, target_path)
        if existing is not None and (
            not existing.isdir() or existing.uid != conversation_uid
        ):
            raise RuntimeError("对话工作区所有者与 UID 注册表不一致")

        conversation_name = str(conversation_id)
        self.put(
            container,
            SANDBOX_WORKSPACE_ROOT,
            [
                (conversation_name, conversation_uid, conversation_uid, 0o750),
                (
                    f"{conversation_name}/.home",
                    conversation_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{conversation_name}/.cache",
                    conversation_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{conversation_name}/.cache/uv",
                    conversation_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{conversation_name}/.tmp",
                    conversation_uid,
                    conversation_uid,
                    0o700,
                ),
            ],
            [],
        )
        self.put(
            container,
            SANDBOX_STAGING_ROOT,
            [
                (conversation_name, 0, 0, 0o700),
                (f"{conversation_name}/{conversation_uid}", 0, 0, 0o700),
            ],
            [],
        )
        return conversation_uid

    def ensure_session_workspace(
        self,
        container: Container,
        conversation_id: UUID,
        scope: SandboxSessionScope,
    ) -> tuple[int, int]:
        """创建 Agent Session 目录并返回 conversation/session UID"""
        conversation_uid = self.ensure_workspace(container, conversation_id)
        registry = self._load_registry(container)
        registry_key = scope.registry_key(conversation_id)
        session_uid = registry.sessions.get(registry_key)
        if session_uid is None:
            session_uid = self._allocate_uid(
                f"session:{registry_key}".encode(),
                {*registry.conversations.values(), *registry.sessions.values()},
            )
            registry.sessions[registry_key] = session_uid
            self._write_registry(container, registry)

        conversation_name = str(conversation_id)
        session_relative = scope.relative_workspace
        session_path = posixpath.join(
            SANDBOX_WORKSPACE_ROOT,
            conversation_name,
            session_relative,
        )
        existing = self.inspect_path(container, session_path)
        if existing is not None and (
            not existing.isdir()
            or existing.uid not in {conversation_uid, session_uid}
            or existing.gid != conversation_uid
        ):
            raise RuntimeError("Agent Session 工作区所有者无效")

        analysis_root = f"analyses/{scope.analysis_id}"
        sessions_root = f"{analysis_root}/sessions"
        agent_root = f"{sessions_root}/{scope.agent_type}"
        self.put(
            container,
            f"{SANDBOX_WORKSPACE_ROOT}/{conversation_name}",
            [
                ("analyses", conversation_uid, conversation_uid, 0o750),
                (analysis_root, conversation_uid, conversation_uid, 0o750),
                (sessions_root, conversation_uid, conversation_uid, 0o750),
                (agent_root, conversation_uid, conversation_uid, 0o750),
                (session_relative, session_uid, conversation_uid, 0o750),
                (f"{session_relative}/.home", session_uid, conversation_uid, 0o700),
                (f"{session_relative}/.cache", session_uid, conversation_uid, 0o700),
                (f"{session_relative}/.cache/uv", session_uid, conversation_uid, 0o700),
                (f"{session_relative}/.tmp", session_uid, conversation_uid, 0o700),
            ],
            [],
        )
        self.put(
            container,
            posixpath.join(SANDBOX_STAGING_ROOT, conversation_name),
            [(str(session_uid), 0, 0, 0o700)],
            [],
        )
        prepared = self.inspect_path(container, session_path)
        if (
            prepared is None
            or not prepared.isdir()
            or prepared.uid != session_uid
            or prepared.gid != conversation_uid
            or prepared.mode & 0o777 != 0o750
        ):
            raise RuntimeError("Agent Session 工作区权限设置失败")
        return conversation_uid, session_uid

    def delete_session(
        self,
        container: Container,
        conversation_id: UUID,
        scope: SandboxSessionScope,
    ) -> bool:
        """删除 Agent Session 工作区、暂存目录和 UID 映射"""
        registry = self._load_registry(container)
        registry_key = scope.registry_key(conversation_id)
        session_uid = registry.sessions.get(registry_key)
        session_path = posixpath.join(
            SANDBOX_WORKSPACE_ROOT,
            str(conversation_id),
            scope.relative_workspace,
        )
        session_exists = self.inspect_path(container, session_path) is not None
        staging_path = (
            posixpath.join(
                SANDBOX_STAGING_ROOT,
                str(conversation_id),
                str(session_uid),
            )
            if session_uid is not None
            else None
        )
        staging_exists = bool(
            staging_path is not None
            and self.inspect_path(container, staging_path) is not None
        )
        targets = [session_path]
        if staging_path is not None:
            targets.append(staging_path)
        result = container.exec_run(
            ["rm", "-rf", "--", *targets],
            user="0",
            privileged=True,
            workdir="/workspace",
        )
        if result.exit_code != 0:
            raw_output = result.output or b""
            detail = (
                raw_output.decode("utf-8", errors="replace")
                if isinstance(raw_output, bytes)
                else str(raw_output)
            ).strip()
            raise OSError(detail or "删除 Agent Session 沙箱失败")
        mapping_existed = registry.sessions.pop(registry_key, None) is not None
        if mapping_existed:
            self._write_registry(container, registry)
        return session_exists or staging_exists or mapping_existed

    @staticmethod
    def _registered_session_uid(
        registry: _UidRegistry,
        conversation_id: UUID,
        relative_path: str,
    ) -> int | None:
        """返回与产物路径精确绑定的 Session UID"""
        parts = PurePosixPath(relative_path).parts
        if len(parts) < 3 or parts[0] != "analyses" or parts[2] != "sessions":
            return None
        if len(parts) < 6:
            raise SandboxPathError(relative_path)
        try:
            scope = SandboxSessionScope(parts[1], parts[3], parts[4])
        except ValueError as exc:
            raise SandboxPathError(relative_path) from exc
        session_uid = registry.sessions.get(scope.registry_key(conversation_id))
        if session_uid is None:
            raise SandboxPathError(relative_path)
        return session_uid

    def _allowed_file_uids(
        self,
        registry: _UidRegistry,
        conversation_id: UUID,
        conversation_uid: int,
        relative_path: str,
    ) -> set[int]:
        """返回给定会话文件路径允许使用的属主 UID"""
        allowed = {conversation_uid}
        session_uid = self._registered_session_uid(
            registry,
            conversation_id,
            relative_path,
        )
        if session_uid is not None:
            allowed.add(session_uid)
        return allowed

    def _validate_target(
        self,
        container: Container,
        conversation_id: UUID,
        conversation_uid: int,
        relative_path: str,
    ) -> tuple[list[tuple[str, int, int, int]], int]:
        """校验文件路径并返回待创建目录和被替换大小"""
        workspace = f"{SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        registry = self._load_registry(container)
        session_uid = self._registered_session_uid(
            registry,
            conversation_id,
            relative_path,
        )
        root_info = self.inspect_path(container, workspace)
        if (
            root_info is None
            or not root_info.isdir()
            or root_info.uid != conversation_uid
            or root_info.gid != conversation_uid
        ):
            raise OSError("对话工作区无效")

        parts = PurePosixPath(relative_path).parts
        directories: list[tuple[str, int, int, int]] = []
        current_path = workspace
        for index, component in enumerate(parts[:-1], start=1):
            current_path = posixpath.join(current_path, component)
            info = self.inspect_path(container, current_path)
            directory_uid = (
                session_uid
                if session_uid is not None and index >= 5
                else conversation_uid
            )
            if info is None:
                directories.append(
                    ("/".join(parts[:index]), directory_uid, conversation_uid, 0o750)
                )
                continue
            allowed_uids = (
                {conversation_uid, session_uid}
                if session_uid is not None and index >= 5
                else {conversation_uid}
            )
            if (
                not info.isdir()
                or info.uid not in allowed_uids
                or info.gid != conversation_uid
            ):
                raise SandboxPathError(relative_path)

        target_info = self.inspect_path(
            container, posixpath.join(workspace, relative_path)
        )
        if target_info is None:
            return directories, 0
        allowed_target_uids = {conversation_uid}
        if session_uid is not None:
            allowed_target_uids.add(session_uid)
        if (
            not target_info.isreg()
            or target_info.uid not in allowed_target_uids
            or target_info.gid != conversation_uid
        ):
            raise SandboxPathError(relative_path)
        return directories, target_info.size

    def _workspace_size(
        self,
        container: Container,
        conversation_id: UUID,
        conversation_uid: int,
    ) -> int:
        """流式统计会话工作区普通文件大小"""
        workspace = f"{SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        registry = self._load_registry(container)
        session_prefix = f"{conversation_id}/"
        allowed_uids = {
            conversation_uid,
            *(
                uid
                for key, uid in registry.sessions.items()
                if key.startswith(session_prefix)
            ),
        }
        total = 0
        with self.open_archive(container, workspace) as archive:
            for member in archive:
                if member.isreg():
                    if member.uid not in allowed_uids or member.gid != conversation_uid:
                        raise OSError("对话工作区包含无效的所有者")
                    total += member.size
                    if total > self._max_workspace_bytes:
                        break
        return total

    def upload_file(
        self,
        container: Container,
        conversation_id: UUID,
        relative_path: str,
        content: BinaryIO,
    ) -> None:
        """上传并校验会话文件"""
        conversation_uid = self.ensure_workspace(container, conversation_id)
        content.seek(0, io.SEEK_END)
        size = content.tell()
        content.seek(0)
        if size > self._max_file_bytes:
            raise SandboxFileTooLargeError(
                f"文件大小超出限制: {size} > {self._max_file_bytes}"
            )
        directories, replaced_size = self._validate_target(
            container,
            conversation_id,
            conversation_uid,
            relative_path,
        )
        projected_size = (
            self._workspace_size(container, conversation_id, conversation_uid)
            - replaced_size
            + size
        )
        if projected_size > self._max_workspace_bytes:
            raise SandboxStorageLimitError(
                f"工作区容量超出限制: {projected_size} > {self._max_workspace_bytes}"
            )
        workspace = f"{SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        self.put(
            container,
            workspace,
            directories,
            [
                (
                    relative_path,
                    conversation_uid,
                    conversation_uid,
                    0o640,
                    content,
                    size,
                )
            ],
        )
        written = self.inspect_path(container, posixpath.join(workspace, relative_path))
        if (
            written is None
            or not written.isreg()
            or written.uid != conversation_uid
            or written.size != size
        ):
            raise OSError("上传附件未通过校验")

    def download_file(
        self,
        container: Container,
        conversation_id: UUID,
        relative_path: str,
    ) -> bytes:
        """下载并校验会话文件"""
        conversation_uid = self.ensure_workspace(container, conversation_id)
        self._validate_target(
            container, conversation_id, conversation_uid, relative_path
        )
        workspace = f"{SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        content, member = self.read_file(
            container,
            posixpath.join(workspace, relative_path),
            self._max_file_bytes,
        )
        allowed_uids = self._allowed_file_uids(
            self._load_registry(container),
            conversation_id,
            conversation_uid,
            relative_path,
        )
        if member.uid not in allowed_uids or member.gid != conversation_uid:
            raise FileNotFoundError(relative_path)
        return content

    def is_file(
        self,
        container: Container,
        conversation_id: UUID,
        relative_path: str,
    ) -> bool:
        """检查路径是否为当前会话可访问的普通文件"""
        conversation_uid = self.ensure_workspace(container, conversation_id)
        try:
            self._validate_target(
                container,
                conversation_id,
                conversation_uid,
                relative_path,
            )
            allowed_uids = self._allowed_file_uids(
                self._load_registry(container),
                conversation_id,
                conversation_uid,
                relative_path,
            )
        except SandboxPathError:
            return False
        target = self.inspect_path(
            container,
            posixpath.join(SANDBOX_WORKSPACE_ROOT, str(conversation_id), relative_path),
        )
        return bool(
            target is not None
            and target.isreg()
            and target.uid in allowed_uids
            and target.gid == conversation_uid
        )

    def delete_conversation(
        self,
        container: Container,
        conversation_id: UUID,
    ) -> None:
        """删除会话工作区并更新 UID 注册表"""
        self.ensure_workspace(container, conversation_id)
        result = container.exec_run(
            [
                "rm",
                "-rf",
                "--",
                f"{SANDBOX_WORKSPACE_ROOT}/{conversation_id}",
                posixpath.join(SANDBOX_STAGING_ROOT, str(conversation_id)),
            ],
            user="0",
            privileged=True,
            workdir="/workspace",
        )
        if result.exit_code != 0:
            raw_output = result.output or b""
            detail = (
                raw_output.decode("utf-8", errors="replace")
                if isinstance(raw_output, bytes)
                else str(raw_output)
            ).strip()
            raise OSError(detail or "删除对话沙箱失败")
        registry = self._load_registry(container)
        registry.conversations.pop(str(conversation_id), None)
        session_prefix = f"{conversation_id}/"
        registry.sessions = {
            key: value
            for key, value in registry.sessions.items()
            if not key.startswith(session_prefix)
        }
        self._write_registry(container, registry)
