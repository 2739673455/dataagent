"""Docker 沙箱持久工作区归档操作。"""

import hashlib
import io
import json
import posixpath
import tarfile
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from uuid import UUID

from docker.errors import NotFound
from docker.models.containers import Container

from app.sandbox.errors import (
    SandboxFileTooLargeError,
    SandboxPathError,
)
from app.sandbox.paths import (
    SANDBOX_DATA_ROOT,
    SANDBOX_STAGING_ROOT,
)

_SANDBOX_UID_REGISTRY = f"{SANDBOX_DATA_ROOT}/.dataagent-uids.json"
_UID_REGISTRY_VERSION = 3
_MIN_SANDBOX_UID = 100_000
_MAX_SANDBOX_UID = 2_147_483_646
_ARCHIVE_SPOOL_BYTES = 8 * 1024 * 1024


@dataclass(slots=True)
class _UidRegistry:
    """持久化会话的 Linux UID。"""

    conversations: dict[str, int]


class _IteratorReader(io.RawIOBase):
    """将 Docker archive 字节迭代器适配为 tarfile 可读取的流。"""

    def __init__(self, chunks: Any) -> None:
        """绑定 Docker archive 返回的字节块迭代器。"""
        super().__init__()
        self._chunks = chunks
        self._buffer = bytearray()
        self._finished = False

    def readable(self) -> bool:
        """声明该适配器支持读取。"""
        return True

    def readinto(self, target: Any) -> int:
        """将迭代器数据填充到目标缓冲区。"""
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
        """关闭底层字节迭代器和读取流。"""
        close_chunks = getattr(self._chunks, "close", None)
        if callable(close_chunks):
            close_chunks()
        super().close()


class SandboxArchiveStore:
    """管理停止或运行容器中的持久工作区和文件归档。"""

    def __init__(self, max_file_bytes: int) -> None:
        """初始化单文件大小限制。"""
        self._max_file_bytes = max_file_bytes

    def ensure_workspace(
        self,
        container: Container,
        conversation_id: UUID,
        registry: _UidRegistry | None = None,
    ) -> int:
        """创建会话工作区并返回稳定 UID。"""
        self._put(
            container,
            SANDBOX_DATA_ROOT,
            [
                (PurePosixPath(SANDBOX_STAGING_ROOT).name, 0, 0, 0o700),
            ],
            [],
        )
        registry = registry or self._load_registry(container)
        key = str(conversation_id)
        conversation_uid = registry.conversations.get(key)
        if conversation_uid is None:
            conversation_uid = self._allocate_uid(
                conversation_id.bytes,
                set(registry.conversations.values()),
            )
            registry.conversations[key] = conversation_uid
            self._write_registry(container, registry)

        target_path = f"{SANDBOX_DATA_ROOT}/{conversation_id}"
        existing = self._inspect_path(container, target_path)
        if existing is not None and (
            not existing.isdir() or existing.uid != conversation_uid
        ):
            raise RuntimeError("对话工作区所有者与 UID 注册表不一致")

        conversation_name = str(conversation_id)
        self._put(
            container,
            SANDBOX_DATA_ROOT,
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
        self._put(
            container,
            SANDBOX_STAGING_ROOT,
            [
                (conversation_name, 0, 0, 0o700),
                (f"{conversation_name}/{conversation_uid}", 0, 0, 0o700),
            ],
            [],
        )
        return conversation_uid

    def upload_file(
        self,
        container: Container,
        conversation_id: UUID,
        relative_path: str,
        content: BinaryIO,
    ) -> None:
        """上传并校验会话文件。"""
        registry = self._load_registry(container)
        conversation_uid = self.ensure_workspace(container, conversation_id, registry)
        content.seek(0, io.SEEK_END)
        size = content.tell()
        content.seek(0)
        if size > self._max_file_bytes:
            raise SandboxFileTooLargeError(
                detail=f"文件大小超出限制: {size} > {self._max_file_bytes}"
            )
        directories, _ = self._validate_target(
            container,
            conversation_id,
            conversation_uid,
            relative_path,
        )
        workspace = f"{SANDBOX_DATA_ROOT}/{conversation_id}"
        self._put(
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
        written = self._inspect_path(
            container, posixpath.join(workspace, relative_path)
        )
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
        """下载并校验会话文件。"""
        registry = self._load_registry(container)
        conversation_uid = self._existing_workspace_uid(
            container, conversation_id, registry
        )
        if conversation_uid is None:
            raise FileNotFoundError(relative_path)
        self._validate_target(
            container, conversation_id, conversation_uid, relative_path
        )
        workspace = f"{SANDBOX_DATA_ROOT}/{conversation_id}"
        content, member = self._read_file(
            container,
            posixpath.join(workspace, relative_path),
            self._max_file_bytes,
        )
        if member.uid != conversation_uid or member.gid != conversation_uid:
            raise FileNotFoundError(relative_path)
        return content

    def is_downloadable_file(
        self,
        container: Container,
        conversation_id: UUID,
        relative_path: str,
    ) -> bool:
        """检查路径是否为当前会话可下载的普通文件。"""
        target = self._accessible_file(container, conversation_id, relative_path)
        return target is not None and target.size <= self._max_file_bytes

    def delete_conversation(
        self,
        container: Container,
        conversation_id: UUID,
    ) -> None:
        """删除会话工作区并更新 UID 注册表。"""
        registry = self._load_registry(container)
        result = container.exec_run(
            [
                "rm",
                "-rf",
                "--",
                f"{SANDBOX_DATA_ROOT}/{conversation_id}",
                posixpath.join(SANDBOX_STAGING_ROOT, str(conversation_id)),
            ],
            user="0",
            privileged=True,
            workdir=SANDBOX_DATA_ROOT,
        )
        if result.exit_code != 0:
            raw_output = result.output or b""
            detail = (
                raw_output.decode("utf-8", errors="replace")
                if isinstance(raw_output, bytes)
                else str(raw_output)
            ).strip()
            raise OSError(detail or "删除对话沙箱失败")
        registry.conversations.pop(str(conversation_id), None)
        self._write_registry(container, registry)

    @contextmanager
    def _open_archive(
        self,
        container: Container,
        path: str,
    ) -> Generator[tarfile.TarFile]:
        """流式打开容器中的 archive。"""
        chunks, _ = container.get_archive(path)
        raw_reader = _IteratorReader(iter(chunks))
        buffered_reader = io.BufferedReader(raw_reader)
        try:
            with tarfile.open(fileobj=buffered_reader, mode="r|*") as archive:
                yield archive
        finally:
            buffered_reader.close()

    def _inspect_path(self, container: Container, path: str) -> tarfile.TarInfo | None:
        """读取容器路径对应的首个 archive 条目。"""
        try:
            with self._open_archive(container, path) as archive:
                return next(iter(archive), None)
        except NotFound:
            return None

    def _read_file(
        self,
        container: Container,
        path: str,
        max_bytes: int,
    ) -> tuple[bytes, tarfile.TarInfo]:
        """从容器读取一个限长普通文件。"""
        with self._open_archive(container, path) as archive:
            member = next(iter(archive), None)
            if member is None or not member.isreg():
                raise FileNotFoundError(path)
            if member.size > max_bytes:
                raise SandboxFileTooLargeError(
                    detail=f"文件大小超出限制: {member.size} > {max_bytes}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise FileNotFoundError(path)
            content = extracted.read()
            return content, member

    def _put(
        self,
        container: Container,
        base_path: str,
        directories: list[tuple[str, int, int, int]],
        files: list[tuple[str, int, int, int, BinaryIO, int]],
    ) -> None:
        """构造受控 tar 并写入容器。"""
        with tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_BYTES) as buffer:
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                for name, owner_uid, owner_gid, mode in directories:
                    info = tarfile.TarInfo(name=name.rstrip("/") + "/")
                    info.mtime = int(time.time())
                    info.type = tarfile.DIRTYPE
                    info.mode = mode
                    info.uid = owner_uid
                    info.gid = owner_gid
                    archive.addfile(info)
                for name, owner_uid, owner_gid, mode, content, size in files:
                    info = tarfile.TarInfo(name=name)
                    info.mtime = int(time.time())
                    info.size = size
                    info.mode = mode
                    info.uid = owner_uid
                    info.gid = owner_gid
                    archive.addfile(info, content)
            buffer.seek(0)
            if not container.put_archive(base_path, buffer):
                raise OSError(f"写入 Docker 归档失败: {base_path}")

    def _write_registry(self, container: Container, registry: _UidRegistry) -> None:
        """将 UID 注册表持久化到用户数据卷。"""
        content = json.dumps(
            {
                "version": _UID_REGISTRY_VERSION,
                "conversations": registry.conversations,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self._put(
            container,
            SANDBOX_DATA_ROOT,
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
    def _validate_registry(registry: _UidRegistry) -> None:
        """校验用户卷内的会话 UID 唯一且处于允许范围。"""
        values = list(registry.conversations.values())
        if len(values) != len(set(values)):
            raise RuntimeError("沙箱 UID 注册表包含重复的 UID")
        if any(uid < _MIN_SANDBOX_UID or uid > _MAX_SANDBOX_UID for uid in values):
            raise RuntimeError("沙箱 UID 注册表包含无效的 UID")

    def _load_registry(self, container: Container) -> _UidRegistry:
        """读取 UID 注册表并校验格式及文件属主。"""
        try:
            content, member = self._read_file(
                container,
                _SANDBOX_UID_REGISTRY,
                4 * 1024 * 1024,
            )
        except (NotFound, FileNotFoundError):
            registry = _UidRegistry(conversations={})
            self._write_registry(container, registry)
            return registry
        if member.uid != 0:
            raise RuntimeError("沙箱 UID 注册表文件拥有者无效")
        payload = json.loads(content)
        if payload.get("version") != _UID_REGISTRY_VERSION:
            raise RuntimeError("不支持的沙箱 UID 注册表版本")
        raw_conversations = payload.get("conversations")
        if not isinstance(raw_conversations, dict):
            raise TypeError("沙箱 UID 注册表格式无效")
        registry = _UidRegistry(
            conversations={
                str(UUID(key)): int(value) for key, value in raw_conversations.items()
            },
        )
        self._validate_registry(registry)
        return registry

    @staticmethod
    def _allocate_uid(seed: bytes, used_uids: set[int]) -> int:
        """根据稳定种子确定性分配未使用的 Linux UID。"""
        uid_range = _MAX_SANDBOX_UID - _MIN_SANDBOX_UID + 1
        for attempt in range(uid_range):
            digest = hashlib.blake2s(seed + attempt.to_bytes(8, "big")).digest()
            candidate = _MIN_SANDBOX_UID + int.from_bytes(digest[:8], "big") % uid_range
            if candidate not in used_uids:
                return candidate
        raise RuntimeError("沙箱 UID 分配范围已耗尽")

    def _validate_target(
        self,
        container: Container,
        conversation_id: UUID,
        conversation_uid: int,
        relative_path: str,
    ) -> tuple[list[tuple[str, int, int, int]], int]:
        """校验文件路径并返回待创建目录和被替换大小。"""
        workspace = f"{SANDBOX_DATA_ROOT}/{conversation_id}"
        root_info = self._inspect_path(container, workspace)
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
            info = self._inspect_path(container, current_path)
            if info is None:
                directories.append(
                    ("/".join(parts[:index]), conversation_uid, conversation_uid, 0o750)
                )
                continue
            if (
                not info.isdir()
                or info.uid != conversation_uid
                or info.gid != conversation_uid
            ):
                raise SandboxPathError(detail=relative_path)

        target_info = self._inspect_path(
            container, posixpath.join(workspace, relative_path)
        )
        if target_info is None:
            return directories, 0
        if (
            not target_info.isreg()
            or target_info.uid != conversation_uid
            or target_info.gid != conversation_uid
        ):
            raise SandboxPathError(detail=relative_path)
        return directories, target_info.size

    def _existing_workspace_uid(
        self,
        container: Container,
        conversation_id: UUID,
        registry: _UidRegistry,
    ) -> int | None:
        """返回已存在且与注册表一致的 Conversation UID。"""
        conversation_uid = registry.conversations.get(str(conversation_id))
        if conversation_uid is None:
            return None
        root = self._inspect_path(container, f"{SANDBOX_DATA_ROOT}/{conversation_id}")
        if (
            root is None
            or not root.isdir()
            or root.uid != conversation_uid
            or root.gid != conversation_uid
        ):
            return None
        return conversation_uid

    def _accessible_file(
        self,
        container: Container,
        conversation_id: UUID,
        relative_path: str,
    ) -> tarfile.TarInfo | None:
        """读取当前会话可访问的普通文件条目。"""
        registry = self._load_registry(container)
        conversation_uid = self._existing_workspace_uid(
            container, conversation_id, registry
        )
        if conversation_uid is None:
            return None
        try:
            self._validate_target(
                container,
                conversation_id,
                conversation_uid,
                relative_path,
            )
        except SandboxPathError:
            return None
        target = self._inspect_path(
            container,
            posixpath.join(SANDBOX_DATA_ROOT, str(conversation_id), relative_path),
        )
        if (
            target is not None
            and target.isreg()
            and target.uid == conversation_uid
            and target.gid == conversation_uid
        ):
            return target
        return None
