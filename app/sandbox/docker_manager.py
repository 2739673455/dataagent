"""本地 Docker 沙盒管理"""

import asyncio
import base64
import hashlib
import io
import json
import posixpath
import secrets
import shlex
import tarfile
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, TypeVar
from uuid import UUID

from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    DeleteResult,
    EditResult,
    ExecuteOffloadResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox
from docker.errors import APIError, NotFound
from docker.models.containers import Container
from loguru import logger

import docker
from app.shared.config.app_config import ROOT_DIR, SandboxConfig, cfg

_DEPLOYMENT_LABEL = "dataagent.sandbox.deployment"
_USER_LABEL = "dataagent.sandbox.user_id"
_QUOTA_MODE_LABEL = "dataagent.sandbox.quota_mode"
_QUOTA_BYTES_LABEL = "dataagent.sandbox.quota_bytes"
_SANDBOX_WORKSPACE_ROOT = "/workspace/conversations"
_MIN_CONVERSATION_UID = 100_000
_MAX_CONVERSATION_UID = 2_147_483_646
_CONTAINER_SPEC_LABEL = "dataagent.sandbox.spec"
_PATH_MAX_BYTES = 4096
_PATH_COMPONENT_MAX_BYTES = 255
_SANDBOX_STAGING_ROOT = "/workspace/.dataagent-staging"
_SANDBOX_UID_REGISTRY = "/workspace/.dataagent-uids.json"
_SANDBOX_ACTIVITY_FILE = "/workspace/.dataagent-activity.json"
_UID_REGISTRY_VERSION = 2
_ACTIVITY_FILE_VERSION = 1
_ARCHIVE_SPOOL_BYTES = 8 * 1024 * 1024
_CAPACITY_WAIT_POLL_SECONDS = 0.25

_ResultT = TypeVar("_ResultT")

_COMMIT_UPLOAD_SCRIPT = """
import base64
import json
import os
import stat
import sys

payload = json.loads(base64.b64decode(sys.argv[1]).decode())
root = payload["root"]
source = payload["source"]
owner_uid = int(payload["owner_uid"])
owner_gid = int(payload["owner_gid"])
file_mode = int(payload["file_mode"])
directory_mode = int(payload["directory_mode"])
parts = payload["relative_target"].split("/")
source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
try:
    if not stat.S_ISREG(os.fstat(source_fd).st_mode):
        raise OSError("invalid staged file")
    os.fchown(source_fd, owner_uid, owner_gid)
    os.fchmod(source_fd, file_mode)
finally:
    os.close(source_fd)
root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
directory_fd = os.dup(root_fd)
try:
    root_stat = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != owner_uid
        or root_stat.st_gid != owner_gid
    ):
        raise PermissionError("invalid workspace owner")
    for component in parts[:-1]:
        created = False
        try:
            os.mkdir(component, mode=directory_mode, dir_fd=directory_fd)
            created = True
        except FileExistsError:
            pass
        next_fd = os.open(
            component,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        if created:
            os.fchown(next_fd, owner_uid, owner_gid)
            os.fchmod(next_fd, directory_mode)
        next_stat = os.fstat(next_fd)
        if next_stat.st_uid != owner_uid or next_stat.st_gid != owner_gid:
            raise PermissionError("invalid target directory owner")
        os.close(directory_fd)
        directory_fd = next_fd
    os.replace(
        source,
        parts[-1],
        src_dir_fd=None,
        dst_dir_fd=directory_fd,
    )
finally:
    os.close(directory_fd)
    os.close(root_fd)
""".strip()

_LARGE_EDIT_SCRIPT = """
import base64
import json
import os
import stat
import sys

payload = json.loads(base64.b64decode(sys.argv[1]).decode())
try:
    with open(payload["old"], "rb") as old_file:
        old = old_file.read().decode("utf-8")
    with open(payload["new"], "rb") as new_file:
        new = new_file.read().decode("utf-8")
    target_stat = os.stat(payload["target"])
    if not stat.S_ISREG(target_stat.st_mode):
        print(json.dumps({"error": "not_a_file"}))
        sys.exit(0)
    with open(payload["target"], "rb") as target_file:
        content = target_file.read().decode("utf-8")

    old_crlf = old.replace("\\r\\n", "\\n").replace("\\n", "\\r\\n")
    old_lf = old.replace("\\r\\n", "\\n")
    new_crlf = new.replace("\\r\\n", "\\n").replace("\\n", "\\r\\n")
    new_lf = new.replace("\\r\\n", "\\n")
    count = 0
    matched_old, matched_new = old, new
    for candidate_old, candidate_new in (
        (old, new),
        (old_crlf, new_crlf),
        (old_lf, new_lf),
    ):
        candidate_count = content.count(candidate_old)
        if candidate_count:
            matched_old = candidate_old
            matched_new = candidate_new
            count = candidate_count
            break
    if count == 0:
        print(json.dumps({"error": "string_not_found"}))
    elif count > 1 and not payload["replace_all"]:
        print(json.dumps({"error": "multiple_occurrences", "count": count}))
    else:
        updated = (
            content.replace(matched_old, matched_new)
            if payload["replace_all"]
            else content.replace(matched_old, matched_new, 1)
        )
        updated_bytes = updated.encode("utf-8")
        if len(updated_bytes) > payload["max_file_bytes"]:
            print(json.dumps({"error": "file_too_large"}))
            sys.exit(0)
        workspace_size = 0
        for current_root, _, files in os.walk(payload["workspace"]):
            for name in files:
                current_path = os.path.join(current_root, name)
                try:
                    if os.path.isfile(current_path) and not os.path.islink(current_path):
                        workspace_size += os.path.getsize(current_path)
                except OSError:
                    pass
        projected_size = len(updated_bytes) + workspace_size - len(content.encode("utf-8"))
        if projected_size > payload["max_workspace_bytes"]:
            print(json.dumps({"error": "workspace_limit_exceeded"}))
            sys.exit(0)
        with open(payload["target"], "wb") as target_file:
            target_file.write(updated_bytes)
        print(json.dumps({"count": count}))
except FileNotFoundError:
    print(json.dumps({"error": "file_not_found"}))
except PermissionError:
    print(json.dumps({"error": "permission_denied"}))
except UnicodeDecodeError:
    print(json.dumps({"error": "not_a_text_file"}))
finally:
    for path in (payload["old"], payload["new"]):
        try:
            os.remove(path)
        except OSError:
            pass
""".strip()


class SandboxPathError(ValueError):
    """沙盒路径非法"""


class SandboxFileTooLargeError(OSError):
    """沙盒文件超过大小限制"""


class SandboxStorageLimitError(OSError):
    """沙盒工作区超过容量限制"""


class SandboxDeletedError(RuntimeError):
    """沙盒资源已被删除"""


class SandboxCapacityError(RuntimeError):
    """沙盒运行容量不可用"""


class SandboxCapacityTimeoutError(SandboxCapacityError):
    """等待沙盒运行容量超时"""


class SandboxCapacityQueueFullError(SandboxCapacityError):
    """沙盒容量等待队列已满"""


class SandboxCapacityClosedError(SandboxCapacityError):
    """沙盒容量调度器已关闭"""


class SandboxCapacityCancelledError(SandboxCapacityError):
    """沙盒容量等待已取消"""


@dataclass(frozen=True, slots=True)
class SandboxSessionScope:
    """定位一个专业 Agent Session 工作区"""

    analysis_id: str
    agent_type: str
    session_id: str

    def __post_init__(self) -> None:
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
                raise ValueError(f"invalid sandbox session {field_name}")

    @property
    def relative_workspace(self) -> str:
        """生成 conversation 根目录下的 Session 路径"""
        return (
            f"analyses/{self.analysis_id}/sessions/{self.agent_type}/{self.session_id}"
        )

    def registry_key(self, conversation_id: UUID) -> str:
        """生成 UID 注册表中的稳定 Session 键"""
        return f"{conversation_id}/{self.relative_workspace}"


@dataclass(slots=True)
class _SandboxUidRegistry:
    """持久化 conversation 和 Agent Session 的 Linux UID"""

    conversations: dict[str, int]
    sessions: dict[str, int]


@dataclass(frozen=True, slots=True)
class SandboxCapacitySnapshot:
    """沙盒容量调度状态快照"""

    running: int
    reserved: int
    waiting: int
    max_running: int
    max_waiting: int
    closed: bool


@dataclass(frozen=True, slots=True)
class DockerSandboxHealth:
    """Docker 沙盒管理器健康状态"""

    cleanup_task_running: bool
    last_cleanup_started_at: float | None
    last_cleanup_completed_at: float | None
    cleanup_consecutive_failures: int
    cleanup_last_error: str | None
    quota_mode: str
    capacity: SandboxCapacitySnapshot


@dataclass(eq=False, slots=True)
class _CapacityWaiter:
    """公平容量队列中的等待项"""

    user_id: int
    deadline: float
    cancel_event: threading.Event | None
    cancelled: bool = False


class _FairCapacityLimiter:
    """提供有界 FIFO 等待、超时和取消的运行容器容量限制器"""

    def __init__(
        self,
        max_running: int,
        max_waiting: int,
        wait_timeout_seconds: float,
    ) -> None:
        self._max_running = max_running
        self._max_waiting = max_waiting
        self._wait_timeout_seconds = wait_timeout_seconds
        self._condition = threading.Condition()
        self._running_users: set[int] = set()
        self._reserved_users: set[int] = set()
        self._waiters: deque[_CapacityWaiter] = deque()
        self._closed = False

    def _remove_waiter_unlocked(self, waiter: _CapacityWaiter) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            return
        self._condition.notify_all()

    def acquire(
        self,
        user_id: int,
        idle_priority: Callable[[int], float],
        evict_idle_user: Callable[[int], bool],
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """公平等待运行槽位并返回是否创建了新预留"""
        waiter: _CapacityWaiter | None = None
        deadline = time.monotonic() + self._wait_timeout_seconds
        try:
            while True:
                candidates: list[int] = []
                with self._condition:
                    if self._closed:
                        raise SandboxCapacityClosedError(
                            "Docker 沙箱容量限制器已关闭"
                        )
                    if (
                        waiter is not None
                        and waiter.cancelled
                        or cancel_event is not None
                        and cancel_event.is_set()
                    ):
                        raise SandboxCapacityCancelledError(
                            "Docker 沙箱容量排队等待已取消"
                        )
                    if user_id in self._running_users:
                        return False
                    if waiter is None:
                        occupied = len(self._running_users) + len(self._reserved_users)
                        if occupied < self._max_running and not self._waiters:
                            self._reserved_users.add(user_id)
                            return True
                        if len(self._waiters) >= self._max_waiting:
                            raise SandboxCapacityQueueFullError(
                                "Docker 沙箱容量等待队列已满"
                            )
                        waiter = _CapacityWaiter(
                            user_id=user_id,
                            deadline=deadline,
                            cancel_event=cancel_event,
                        )
                        self._waiters.append(waiter)

                    remaining = waiter.deadline - time.monotonic()
                    if remaining <= 0:
                        raise SandboxCapacityTimeoutError(
                            "等待 Docker 沙箱运行容量超时"
                        )
                    is_head = bool(self._waiters and self._waiters[0] is waiter)
                    occupied = len(self._running_users) + len(self._reserved_users)
                    if is_head and occupied < self._max_running:
                        self._waiters.popleft()
                        waiter = None
                        self._reserved_users.add(user_id)
                        self._condition.notify_all()
                        return True
                    if is_head:
                        candidates = sorted(
                            self._running_users,
                            key=idle_priority,
                        )

                if candidates and any(
                    evict_idle_user(candidate) for candidate in candidates
                ):
                    continue
                with self._condition:
                    if waiter is None:
                        continue
                    remaining = waiter.deadline - time.monotonic()
                    if remaining <= 0:
                        continue
                    self._condition.wait(
                        timeout=min(_CAPACITY_WAIT_POLL_SECONDS, remaining)
                    )
        finally:
            if waiter is not None:
                with self._condition:
                    self._remove_waiter_unlocked(waiter)

    def complete_reservation(self, user_id: int, *, running: bool) -> None:
        """提交或回滚一个运行槽位预留"""
        with self._condition:
            self._reserved_users.discard(user_id)
            if running:
                self._running_users.add(user_id)
            else:
                self._running_users.discard(user_id)
            self._condition.notify_all()

    def mark_running(self, user_id: int) -> None:
        """登记已经运行的用户容器"""
        with self._condition:
            self._running_users.add(user_id)
            self._condition.notify_all()

    def mark_not_running(self, user_id: int) -> None:
        """释放用户占用的运行槽位"""
        with self._condition:
            self._running_users.discard(user_id)
            self._reserved_users.discard(user_id)
            self._condition.notify_all()

    def reconcile(self, running_user_ids: list[int]) -> list[int]:
        """登记已有运行容器并返回超过上限的用户"""
        keep = running_user_ids[: self._max_running]
        overflow = running_user_ids[self._max_running :]
        with self._condition:
            self._running_users = set(keep)
            self._reserved_users.clear()
            self._condition.notify_all()
        return overflow

    def cancel_user(self, user_id: int) -> None:
        """取消指定用户的全部容量等待"""
        with self._condition:
            for waiter in self._waiters:
                if waiter.user_id == user_id:
                    waiter.cancelled = True
            self._condition.notify_all()

    def notify_waiters(self) -> None:
        """唤醒等待线程以重新检查取消和容量状态"""
        with self._condition:
            self._condition.notify_all()

    def close(self) -> None:
        """关闭容量限制器并取消全部等待"""
        with self._condition:
            self._closed = True
            for waiter in self._waiters:
                waiter.cancelled = True
            self._condition.notify_all()

    def snapshot(self) -> SandboxCapacitySnapshot:
        """返回当前容量状态快照"""
        with self._condition:
            return SandboxCapacitySnapshot(
                running=len(self._running_users),
                reserved=len(self._reserved_users),
                waiting=len(self._waiters),
                max_running=self._max_running,
                max_waiting=self._max_waiting,
                closed=self._closed,
            )


class _IteratorReader(io.RawIOBase):
    """将 Docker archive 字节迭代器适配为 tarfile 可读取的流"""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        super().__init__()
        self._chunks = chunks
        self._buffer = bytearray()
        self._finished = False

    def readable(self) -> bool:
        """声明流支持读取"""
        return True

    def readinto(self, target: Any) -> int:
        """按需从 Docker 响应流填充目标缓冲区"""
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
        """关闭底层 Docker 响应流"""
        close_chunks = getattr(self._chunks, "close", None)
        if callable(close_chunks):
            close_chunks()
        super().close()


class _LifecycleGuard:
    """协调并发操作与资源维护"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active_operations = 0
        self._maintenance = False
        self._deleted = False
        self._local = threading.local()

    @contextmanager
    def operation(self) -> Generator[None, None, None]:
        """进入可并发执行的资源操作"""
        operation_depth = getattr(self._local, "operation_depth", 0)
        maintenance_depth = getattr(self._local, "maintenance_depth", 0)
        if operation_depth or maintenance_depth:
            self._local.operation_depth = operation_depth + 1
            try:
                yield
            finally:
                self._local.operation_depth -= 1
            return

        with self._condition:
            while self._maintenance:
                self._condition.wait()
            if self._deleted:
                raise SandboxDeletedError("沙箱资源已被删除")
            self._active_operations += 1
        self._local.operation_depth = 1
        try:
            yield
        finally:
            self._local.operation_depth = 0
            with self._condition:
                self._active_operations -= 1
                self._condition.notify_all()

    @contextmanager
    def maintenance(
        self,
        *,
        allow_deleted: bool = False,
    ) -> Generator[None, None, None]:
        """独占资源并等待现有操作完成"""
        if getattr(self._local, "operation_depth", 0):
            raise RuntimeError("无法在活跃操作期间启动维护流程")
        with self._condition:
            while self._maintenance:
                self._condition.wait()
            if self._deleted and not allow_deleted:
                raise SandboxDeletedError("沙箱资源已被删除")
            self._maintenance = True
            while self._active_operations:
                self._condition.wait()
        self._local.maintenance_depth = 1
        try:
            yield
        finally:
            self._local.maintenance_depth = 0
            with self._condition:
                self._maintenance = False
                self._condition.notify_all()

    @contextmanager
    def try_maintenance(self) -> Generator[bool, None, None]:
        """仅在当前没有操作时尝试获取独占维护权"""
        if getattr(self._local, "operation_depth", 0):
            yield False
            return
        with self._condition:
            if self._maintenance or self._active_operations or self._deleted:
                yield False
                return
            self._maintenance = True
        self._local.maintenance_depth = 1
        try:
            yield True
        finally:
            self._local.maintenance_depth = 0
            with self._condition:
                self._maintenance = False
                self._condition.notify_all()

    @property
    def active_operations(self) -> int:
        """获取当前活跃操作数"""
        with self._condition:
            return self._active_operations

    def mark_deleted(self) -> None:
        """阻止资源继续接受新操作"""
        with self._condition:
            self._deleted = True
            self._condition.notify_all()


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


class DockerSandboxBackend(BaseSandbox):
    """将一个用户容器中的会话目录暴露为虚拟文件系统"""

    enable_capture_offload = True

    def __init__(
        self,
        user_id: int,
        conversation_id: UUID,
        conversation_uid: int,
        sandbox_config: SandboxConfig,
        user_guard: _LifecycleGuard,
        conversation_guard: _LifecycleGuard,
        mutation_lock: threading.RLock,
        touch: Callable[[], None],
        get_running_container: Callable[[threading.Event | None], Container],
        notify_capacity_waiters: Callable[[], None],
        *,
        session_scope: SandboxSessionScope | None = None,
        execution_uid: int | None = None,
    ) -> None:
        """初始化会话级 Docker 沙盒后端"""
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._conversation_dir = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        self._session_scope = session_scope
        self._workspace_dir = (
            posixpath.join(
                self._conversation_dir,
                session_scope.relative_workspace,
            )
            if session_scope is not None
            else self._conversation_dir
        )
        self._conversation_uid = conversation_uid
        self._execution_uid = execution_uid or conversation_uid
        self._execution_gid = conversation_uid
        self._file_mode = 0o640 if session_scope is not None else 0o600
        self._directory_mode = 0o750 if session_scope is not None else 0o700
        self._umask = 0o027 if session_scope is not None else 0o077
        self._execute_timeout_seconds = sandbox_config.execute_timeout_seconds
        self._staging_dir = posixpath.join(
            _SANDBOX_STAGING_ROOT,
            str(conversation_id),
            str(self._execution_uid),
        )
        self._max_output_bytes = sandbox_config.max_output_bytes
        self._max_capture_bytes = sandbox_config.max_capture_bytes
        self._max_file_bytes = sandbox_config.max_file_bytes
        self._max_workspace_bytes = sandbox_config.max_workspace_bytes
        self._user_guard = user_guard
        self._conversation_guard = conversation_guard
        self._mutation_lock = mutation_lock
        self._touch = touch
        self._get_running_container = get_running_container
        self._notify_capacity_waiters = notify_capacity_waiters
        self._operation_local = threading.local()

    @property
    def _container(self) -> Container:
        """获取当前操作持有的容器实例"""
        container = getattr(self._operation_local, "container", None)
        if container is None:
            raise RuntimeError("Docker 容器仅在操作期间可用")
        return container

    @property
    def id(self) -> str:
        """获取沙盒后端唯一标识"""
        scope = (
            f":{self._session_scope.relative_workspace}"
            if self._session_scope is not None
            else ""
        )
        return f"docker:{self._user_id}:{self._conversation_id}{scope}"

    @property
    def workspace_dir(self) -> str:
        """获取会话在容器中的实际工作目录"""
        return self._workspace_dir

    def _resolve_path(self, path: str) -> str:
        """将虚拟路径映射到当前会话目录"""
        if "\x00" in path or path.startswith("~"):
            raise SandboxPathError(path)

        if path == self._workspace_dir or path.startswith(f"{self._workspace_dir}/"):
            return path
        if path == self._conversation_dir or path.startswith(
            f"{self._conversation_dir}/"
        ):
            return path

        parts = PurePosixPath(path).parts
        if any(part == ".." for part in parts):
            raise SandboxPathError(path)
        if PurePosixPath(path).is_absolute():
            return posixpath.join(self._conversation_dir, *parts[1:])
        return posixpath.join(self._workspace_dir, *parts)

    def _resolve_mutation_path(self, path: str) -> str:
        """只允许修改当前 Agent Session 工作区"""
        resolved_path = self._resolve_path(path)
        if self._session_scope is not None and not (
            resolved_path == self._workspace_dir
            or resolved_path.startswith(f"{self._workspace_dir}/")
        ):
            raise SandboxPathError(path)
        return resolved_path

    def _to_virtual_path(self, path: str) -> str:
        """将容器路径还原为 Agent 可见的虚拟路径"""
        if path == self._conversation_dir:
            return "/"
        prefix = f"{self._conversation_dir}/"
        if path.startswith(prefix):
            return f"/{path[len(prefix) :]}"
        if not path.startswith("/"):
            normalized_path = PurePosixPath(path).as_posix()
            return f"/{normalized_path}" if normalized_path != "." else "/"
        return path

    def _hide_workspace(self, message: str | None) -> str | None:
        """从错误信息中隐藏容器工作目录"""
        if message is None:
            return None
        return message.replace(self._conversation_dir, "")

    def _map_file_info(self, info: FileInfo) -> FileInfo:
        """转换文件信息中的路径"""
        return FileInfo(**{**info, "path": self._to_virtual_path(info["path"])})

    def _map_grep_match(self, match: GrepMatch) -> GrepMatch:
        """转换搜索结果中的路径"""
        return GrepMatch(**{**match, "path": self._to_virtual_path(match["path"])})

    @contextmanager
    def _operation(self) -> Generator[None, None, None]:
        """在资源生命周期保护下执行沙盒操作"""
        self._touch()
        existing_container = getattr(self._operation_local, "container", None)
        cancel_event = getattr(self._operation_local, "cancel_event", None)
        try:
            with self._user_guard.operation(), self._conversation_guard.operation():
                if existing_container is None:
                    self._operation_local.container = self._get_running_container(
                        cancel_event
                    )
                yield
        finally:
            if existing_container is None and hasattr(
                self._operation_local, "container"
            ):
                del self._operation_local.container
            self._touch()

    async def _run_async(
        self,
        operation: Callable[[], _ResultT],
    ) -> _ResultT:
        """在线程中运行同步操作并向容量等待传播任务取消"""
        cancel_event = threading.Event()

        def run() -> _ResultT:
            self._operation_local.cancel_event = cancel_event
            try:
                return operation()
            finally:
                del self._operation_local.cancel_event

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            return await task
        except asyncio.CancelledError:
            cancel_event.set()
            self._notify_capacity_waiters()
            raise

    def _execute_unlocked(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """流式执行命令并限制宿主机保留的输出"""
        effective_timeout = self._execute_timeout_seconds
        if timeout is not None and timeout > 0:
            effective_timeout = min(timeout, self._execute_timeout_seconds)
        file_limit_blocks = max(1, self._max_file_bytes // 512)
        command_shell = (
            f"umask {self._umask:03o}; ulimit -f {file_limit_blocks}; "
            f"exec /bin/sh -lc {shlex.quote(command)}"
        )
        shell_command = ["/bin/sh", "-lc", command_shell]
        if effective_timeout > 0:
            shell_command = [
                "timeout",
                "--signal=KILL",
                str(effective_timeout),
                *shell_command,
            ]

        docker_client = self._container.client
        if docker_client is None:
            raise RuntimeError("Docker 容器客户端不可用")
        api_client = docker_client.api
        created = api_client.exec_create(
            self._container.id,
            shell_command,
            stdout=True,
            stderr=True,
            user=f"{self._execution_uid}:{self._execution_gid}",
            environment={
                "HOME": f"{self._workspace_dir}/.home",
                "UV_CACHE_DIR": f"{self._workspace_dir}/.cache/uv",
                "XDG_CACHE_HOME": f"{self._workspace_dir}/.cache",
                "TMPDIR": f"{self._workspace_dir}/.tmp",
                "TMP": f"{self._workspace_dir}/.tmp",
                "TEMP": f"{self._workspace_dir}/.tmp",
                "DATAAGENT_CONVERSATION_ROOT": self._conversation_dir,
            },
            workdir=self._workspace_dir,
        )
        exec_id = created["Id"]
        output_tail = bytearray()
        output_size = 0
        output_stream = api_client.exec_start(exec_id, stream=True, demux=False)
        try:
            for chunk in output_stream:
                if isinstance(chunk, tuple):
                    chunk = b"".join(item for item in chunk if isinstance(item, bytes))
                elif isinstance(chunk, int):
                    chunk = bytes([chunk])
                elif isinstance(chunk, str):
                    chunk = chunk.encode()
                output_size += len(chunk)
                output_tail.extend(chunk)
                if len(output_tail) > self._max_output_bytes:
                    del output_tail[: len(output_tail) - self._max_output_bytes]
        finally:
            close_stream = getattr(output_stream, "close", None)
            if callable(close_stream):
                close_stream()
            stream_response = getattr(output_stream, "_response", None)
            if stream_response is not None:
                stream_response.close()

        inspected = api_client.exec_inspect(exec_id)
        output = output_tail.decode("utf-8", errors="replace")
        return ExecuteResponse(
            output=self._hide_workspace(output) or "",
            exit_code=inspected.get("ExitCode"),
            truncated=output_size > self._max_output_bytes,
        )

    def _workspace_size_unlocked(self) -> int:
        """读取当前会话目录占用的字节数"""
        result = self._container.exec_run(
            [
                "timeout",
                "--signal=KILL",
                str(self._execute_timeout_seconds),
                "du",
                "-sb",
                self._conversation_dir,
            ],
            user="0",
            privileged=True,
            workdir="/workspace",
        )
        raw_output = result.output or b""
        output = (
            raw_output.decode("utf-8", errors="replace")
            if isinstance(raw_output, bytes)
            else str(raw_output)
        )
        if result.exit_code != 0:
            detail = self._hide_workspace(output.strip())
            raise OSError(detail or "查询工作区大小失败")
        try:
            return int(output.split(maxsplit=1)[0])
        except ValueError as exc:
            raise OSError("工作区大小响应格式无效") from exc

    def _validate_workspace_capacity_unlocked(
        self,
        incoming_bytes: int,
        replaced_bytes: int = 0,
    ) -> None:
        """校验写入后工作区不会超过容量限制"""
        projected_size = (
            self._workspace_size_unlocked() - replaced_bytes + incoming_bytes
        )
        if projected_size > self._max_workspace_bytes:
            raise SandboxStorageLimitError(
                f"工作区存储空间超出限制: {projected_size} > {self._max_workspace_bytes}"
            )

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """在用户容器的当前会话目录中执行命令"""
        with self._operation():
            if self._workspace_size_unlocked() > self._max_workspace_bytes:
                return ExecuteResponse(
                    output="Workspace storage limit exceeded; delete files before continuing",
                    exit_code=1,
                )
            result = self._execute_unlocked(command, timeout=timeout)
            if self._workspace_size_unlocked() > self._max_workspace_bytes:
                result.output += "\n[Workspace storage limit exceeded; delete files before continuing]"
                result.truncated = True
            return result

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """异步执行命令并支持取消容量等待"""
        return await self._run_async(lambda: self.execute(command, timeout=timeout))

    def execute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int | None = None,
    ) -> ExecuteOffloadResult:
        """在会话目录中对大命令输出进行源端卸载"""
        capture_limit = min(
            max_capture_bytes or self._max_capture_bytes,
            self._max_capture_bytes,
        )
        return super().execute_with_offload(
            command,
            self._resolve_mutation_path(capture_path),
            max_inline_bytes=max_inline_bytes,
            max_capture_bytes=capture_limit,
            timeout=timeout,
        )

    def ls(self, path: str) -> LsResult:
        """列出当前会话目录内容"""
        try:
            resolved_path = self._resolve_path(path)
        except SandboxPathError:
            return LsResult(error=INVALID_PATH)
        with self._operation():
            result = super().ls(resolved_path)
            return LsResult(
                error=self._hide_workspace(result.error),
                entries=(
                    [self._map_file_info(item) for item in result.entries]
                    if result.entries is not None
                    else None
                ),
            )

    async def als(self, path: str) -> LsResult:
        """异步列出当前会话目录内容"""
        return await self._run_async(lambda: self.ls(path))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """读取当前会话文件"""
        try:
            resolved_path = self._resolve_path(file_path)
        except SandboxPathError:
            return ReadResult(error=INVALID_PATH)
        with self._operation():
            result = super().read(resolved_path, offset, limit)
            result.error = self._hide_workspace(result.error)
            return result

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """异步读取当前会话文件"""
        return await self._run_async(lambda: self.read(file_path, offset, limit))

    def write(self, file_path: str, content: str) -> WriteResult:
        """写入当前会话文件"""
        try:
            resolved_path = self._resolve_mutation_path(file_path)
        except SandboxPathError:
            return WriteResult(error=INVALID_PATH)
        with self._operation():
            result = super().write(resolved_path, content)
            return WriteResult(
                error=self._hide_workspace(result.error),
                path=self._to_virtual_path(result.path) if result.path else None,
            )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """异步写入当前会话文件"""
        return await self._run_async(lambda: self.write(file_path, content))

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """编辑当前会话文件"""
        try:
            resolved_path = self._resolve_mutation_path(file_path)
        except SandboxPathError:
            return EditResult(error=INVALID_PATH)
        with self._operation():
            with self._mutation_lock:
                result = self._edit_file(
                    resolved_path,
                    old_string,
                    new_string,
                    replace_all,
                )
            return EditResult(
                error=self._hide_workspace(result.error),
                path=self._to_virtual_path(result.path) if result.path else None,
                occurrences=result.occurrences,
            )

    def _edit_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool,
    ) -> EditResult:
        """通过会话目录内的临时文件安全编辑文本"""
        token = secrets.token_hex(10)
        old_path = self._resolve_mutation_path(f".deepagents_tmp/{token}.old")
        new_path = self._resolve_mutation_path(f".deepagents_tmp/{token}.new")
        responses = self.upload_files(
            [
                (old_path, old_string.encode()),
                (new_path, new_string.encode()),
            ]
        )
        if error := next((item.error for item in responses if item.error), None):
            self._execute_unlocked(
                f"rm -f {shlex.quote(old_path)} {shlex.quote(new_path)}"
            )
            return EditResult(error=f"Error editing file '{file_path}': {error}")

        payload = base64.b64encode(
            json.dumps(
                {
                    "target": file_path,
                    "old": old_path,
                    "new": new_path,
                    "replace_all": replace_all,
                    "workspace": self._workspace_dir,
                    "max_file_bytes": self._max_file_bytes,
                    "max_workspace_bytes": self._max_workspace_bytes,
                }
            ).encode()
        ).decode()
        result = self.execute(
            f"python3 -c {shlex.quote(_LARGE_EDIT_SCRIPT)} {shlex.quote(payload)}"
        )
        try:
            response = json.loads(result.output)
        except json.JSONDecodeError:
            self._execute_unlocked(
                f"rm -f {shlex.quote(old_path)} {shlex.quote(new_path)}"
            )
            detail = result.output.strip() or "unknown error"
            return EditResult(error=f"Error editing file '{file_path}': {detail}")
        if error := response.get("error"):
            return EditResult(error=f"Error editing file '{file_path}': {error}")
        return EditResult(path=file_path, occurrences=response.get("count", 1))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """异步编辑当前会话文件"""
        return await self._run_async(
            lambda: self.edit(
                file_path,
                old_string,
                new_string,
                replace_all,
            )
        )

    def delete(self, file_path: str) -> DeleteResult:
        """删除当前会话文件或目录"""
        try:
            resolved_path = self._resolve_mutation_path(file_path)
        except SandboxPathError:
            return DeleteResult(error=INVALID_PATH)
        with self._operation():
            with self._mutation_lock:
                result = super().delete(resolved_path)
            return DeleteResult(
                error=self._hide_workspace(result.error),
                path=self._to_virtual_path(result.path) if result.path else None,
            )

    async def adelete(self, file_path: str) -> DeleteResult:
        """异步删除当前会话文件或目录"""
        return await self._run_async(lambda: self.delete(file_path))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """搜索当前会话文件内容"""
        try:
            resolved_path = self._resolve_path(path or "/")
        except SandboxPathError:
            return GrepResult(error=INVALID_PATH)
        with self._operation():
            result = super().grep(
                pattern,
                resolved_path,
                glob,
                max_count=max_count,
            )
            return GrepResult(
                error=self._hide_workspace(result.error),
                matches=(
                    [self._map_grep_match(item) for item in result.matches]
                    if result.matches is not None
                    else None
                ),
                truncated=result.truncated,
            )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """异步搜索当前会话文件内容"""
        return await self._run_async(
            lambda: self.grep(
                pattern,
                path,
                glob,
                max_count=max_count,
            )
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """匹配当前会话中的文件"""
        try:
            resolved_path = self._resolve_path(path or "/")
        except SandboxPathError:
            return GlobResult(error=INVALID_PATH)
        with self._operation():
            result = super().glob(pattern, resolved_path)
            return GlobResult(
                error=self._hide_workspace(result.error),
                matches=(
                    [self._map_file_info(item) for item in result.matches]
                    if result.matches is not None
                    else None
                ),
                truncated=result.truncated,
            )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """异步匹配当前会话中的文件"""
        return await self._run_async(lambda: self.glob(pattern, path))

    def _put_archive(self, path: str, content: BinaryIO, size: int) -> None:
        """先写入受保护的暂存目录，再提交到当前可写根"""
        relative_target = posixpath.relpath(path, self._workspace_dir)
        if relative_target == "." or relative_target.startswith("../"):
            raise SandboxPathError(path)
        staging_name = f"upload-{secrets.token_hex(20)}"
        staging_path = posixpath.join(self._staging_dir, staging_name)
        try:
            with io.BytesIO() as archive_buffer:
                with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
                    info = tarfile.TarInfo(name=staging_name)
                    info.size = size
                    info.mode = 0o600
                    info.uid = 0
                    info.gid = 0
                    archive.addfile(info, content)
                archive_buffer.seek(0)
                if not self._container.put_archive(self._staging_dir, archive_buffer):
                    raise OSError(f"暂存上传文件失败: {path}")

            payload = base64.b64encode(
                json.dumps(
                    {
                        "root": self._workspace_dir,
                        "source": staging_path,
                        "owner_uid": self._execution_uid,
                        "owner_gid": self._execution_gid,
                        "file_mode": self._file_mode,
                        "directory_mode": self._directory_mode,
                        "relative_target": relative_target,
                    }
                ).encode()
            ).decode()
            commit_result = self._container.exec_run(
                ["python3", "-c", _COMMIT_UPLOAD_SCRIPT, payload],
                user="0",
                privileged=True,
                workdir="/workspace",
            )
            if commit_result.exit_code != 0:
                raw_output = commit_result.output or b""
                detail = (
                    raw_output.decode("utf-8", errors="replace")
                    if isinstance(raw_output, bytes)
                    else str(raw_output)
                ).strip()
                raise OSError(f"提交上传文件失败: {detail}")
        finally:
            self._container.exec_run(
                ["rm", "-f", "--", staging_path],
                user="0",
                privileged=True,
                workdir="/workspace",
            )

    def _read_file_bytes_unlocked(self, path: str) -> tuple[bytes, int | None]:
        """以会话 UID 限长读取文件，避免 Docker 守护进程绕过权限"""
        docker_client = self._container.client
        if docker_client is None:
            raise RuntimeError("Docker 容器客户端不可用")
        api_client = docker_client.api
        created = api_client.exec_create(
            self._container.id,
            [
                "timeout",
                "--signal=KILL",
                str(self._execute_timeout_seconds),
                "head",
                "-c",
                str(self._max_file_bytes + 1),
                "--",
                path,
            ],
            stdout=True,
            stderr=True,
            user=f"{self._execution_uid}:{self._execution_gid}",
            environment={"HOME": f"{self._workspace_dir}/.home"},
            workdir=self._workspace_dir,
        )
        exec_id = created["Id"]
        output = bytearray()
        output_stream = api_client.exec_start(exec_id, stream=True, demux=False)
        try:
            for chunk in output_stream:
                if isinstance(chunk, tuple):
                    output.extend(
                        b"".join(item for item in chunk if isinstance(item, bytes))
                    )
                elif isinstance(chunk, int):
                    output.append(chunk)
                elif isinstance(chunk, str):
                    output.extend(chunk.encode())
                else:
                    output.extend(chunk)
        finally:
            close_stream = getattr(output_stream, "close", None)
            if callable(close_stream):
                close_stream()
            stream_response = getattr(output_stream, "_response", None)
            if stream_response is not None:
                stream_response.close()
        inspected = api_client.exec_inspect(exec_id)
        return bytes(output), inspected.get("ExitCode")

    def _file_size_unlocked(self, path: str) -> int:
        """读取文件字节数，不存在时返回零"""
        result = self._execute_unlocked(
            f"if [ -f {shlex.quote(path)} ]; then stat -c %s -- {shlex.quote(path)}; else printf 0; fi"
        )
        if result.exit_code != 0:
            raise OSError(result.output.strip() or f"读取文件元数据失败: {path}")
        try:
            return int(result.output.strip())
        except ValueError as exc:
            raise OSError(f"文件大小响应格式无效: {path}") from exc

    def upload_fileobj(self, path: str, content: BinaryIO) -> FileUploadResponse:
        """上传文件对象到当前会话"""
        try:
            resolved_path = self._resolve_mutation_path(path)
            with self._operation(), self._mutation_lock:
                content.seek(0, io.SEEK_END)
                size = content.tell()
                content.seek(0)
                if size > self._max_file_bytes:
                    return FileUploadResponse(
                        path=path,
                        error=f"file_too_large:{self._max_file_bytes}",
                    )
                replaced_size = self._file_size_unlocked(resolved_path)
                self._validate_workspace_capacity_unlocked(size, replaced_size)
                self._put_archive(resolved_path, content, size)
        except SandboxPathError:
            return FileUploadResponse(path=path, error=INVALID_PATH)
        except SandboxStorageLimitError:
            return FileUploadResponse(
                path=path,
                error=f"workspace_limit_exceeded:{self._max_workspace_bytes}",
            )
        except (APIError, OSError, tarfile.TarError) as exc:
            return FileUploadResponse(path=path, error=str(exc))
        return FileUploadResponse(path=path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """批量上传字节内容到当前会话"""
        return [
            self.upload_fileobj(path, io.BytesIO(content)) for path, content in files
        ]

    async def aupload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        """异步批量上传字节内容到当前会话"""
        return await self._run_async(lambda: self.upload_files(files))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """批量下载当前会话文件"""
        responses: list[FileDownloadResponse] = []
        with self._operation():
            for path in paths:
                try:
                    resolved_path = self._resolve_path(path)
                    inspect_result = self._execute_unlocked(
                        f"if [ -d {shlex.quote(resolved_path)} ]; then exit 45; "
                        f"elif [ ! -f {shlex.quote(resolved_path)} ]; then exit 44; "
                        f"else stat -c %s -- {shlex.quote(resolved_path)}; fi"
                    )
                    if inspect_result.exit_code == 44:
                        responses.append(
                            FileDownloadResponse(path=path, error=FILE_NOT_FOUND)
                        )
                        continue
                    if inspect_result.exit_code == 45:
                        responses.append(
                            FileDownloadResponse(path=path, error=IS_DIRECTORY)
                        )
                        continue
                    if inspect_result.exit_code != 0:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=inspect_result.output.strip()
                                or "failed_to_inspect_file",
                            )
                        )
                        continue
                    try:
                        size = int(inspect_result.output.strip())
                    except ValueError:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error="invalid_file_size_response",
                            )
                        )
                        continue
                    if size > self._max_file_bytes:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=f"file_too_large:{self._max_file_bytes}",
                            )
                        )
                        continue
                    content, exit_code = self._read_file_bytes_unlocked(resolved_path)
                    if len(content) > self._max_file_bytes:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=f"file_too_large:{self._max_file_bytes}",
                            )
                        )
                        continue
                    if exit_code != 0:
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                error=content.decode("utf-8", errors="replace").strip()
                                or "failed_to_read_file",
                            )
                        )
                        continue
                    responses.append(FileDownloadResponse(path=path, content=content))
                except SandboxPathError:
                    responses.append(
                        FileDownloadResponse(path=path, error=INVALID_PATH)
                    )
                except NotFound:
                    responses.append(
                        FileDownloadResponse(path=path, error=FILE_NOT_FOUND)
                    )
                except (APIError, OSError) as exc:
                    responses.append(FileDownloadResponse(path=path, error=str(exc)))
        return responses

    async def adownload_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        """异步批量下载当前会话文件"""
        return await self._run_async(lambda: self.download_files(paths))

    def is_file(self, path: str) -> bool:
        """检查当前会话路径是否为文件"""
        resolved_path = self._resolve_path(path)
        with self._operation():
            result = self._execute_unlocked(f"test -f {shlex.quote(resolved_path)}")
            return result.exit_code == 0


class DockerSandboxManager:
    """管理每个用户唯一的本地 Docker 沙盒"""

    def __init__(self, sandbox_config: SandboxConfig) -> None:
        """初始化 Docker 沙盒管理器"""
        self._config = sandbox_config
        self._client: docker.DockerClient | None = None
        self._container_spec: str | None = None
        self._init_lock = asyncio.Lock()
        self._resource_lock = threading.RLock()
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._user_guards: dict[int, _LifecycleGuard] = {}
        self._conversation_guards: dict[tuple[int, UUID], _LifecycleGuard] = {}
        self._mutation_locks: dict[tuple[int, UUID], threading.RLock] = {}
        self._start_locks: dict[int, threading.Lock] = {}
        self._activity_lock = threading.Lock()
        self._last_activity: dict[int, float] = {}
        self._last_persisted_activity: dict[int, float] = {}
        self._capacity = _FairCapacityLimiter(
            max_running=sandbox_config.max_running_containers,
            max_waiting=sandbox_config.max_capacity_waiters,
            wait_timeout_seconds=sandbox_config.capacity_wait_timeout_seconds,
        )
        self._health_lock = threading.Lock()
        self._last_cleanup_started_at: float | None = None
        self._last_cleanup_completed_at: float | None = None
        self._cleanup_consecutive_failures = 0
        self._cleanup_last_error: str | None = None
        self._cleanup_task: asyncio.Task[None] | None = None

    def _get_client(self) -> docker.DockerClient:
        """获取已初始化的 Docker 客户端"""
        if self._client is None:
            raise RuntimeError("Docker 沙箱管理器尚未初始化")
        return self._client

    def _init_sync(self) -> None:
        """连接 Docker 并确保沙盒镜像存在"""
        client = docker.from_env()
        try:
            client.ping()
            build_context = Path(self._config.build_context)
            if not build_context.is_absolute():
                build_context = ROOT_DIR / build_context
            if self._config.rebuild_image:
                logger.info(f"构建 Docker 沙箱镜像: image={self._config.image}")
                build_args = {
                    "NODE_VERSION": self._config.node_version,
                    "NODE_DOWNLOAD_BASE": self._config.node_download_base,
                    "PYPI_INDEX_URL": self._config.pypi_index_url,
                    "NPM_REGISTRY": self._config.npm_registry,
                }
                image, _ = client.images.build(
                    path=str(build_context),
                    tag=self._config.image,
                    rm=True,
                    network_mode=self._config.build_network_mode,
                    buildargs=build_args,
                )
            else:
                image = client.images.get(self._config.image)
            if self._config.workspace_quota_mode == "application":
                logger.warning(
                    "Docker 沙箱使用应用层强制的工作区配额；宿主机磁盘占用可能临时超过设定限制"
                )
            if image.id is None:
                raise RuntimeError("Docker 沙箱镜像缺少不可变 ID")
            self._container_spec = self._container_spec_digest(image.id)
        except Exception:
            client.close()
            raise
        self._client = client

    async def init(self) -> None:
        """初始化 Docker 沙盒管理器"""
        async with self._init_lock:
            if self._client is None:
                await asyncio.to_thread(self._init_sync)
                await asyncio.to_thread(self._reconcile_running_containers_sync)
            if self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(
                    self._cleanup_idle_containers()
                )

    async def _get_resources(
        self,
        user_id: int,
        conversation_id: UUID | None = None,
    ) -> tuple[
        asyncio.Lock,
        _LifecycleGuard,
        threading.Lock,
        _LifecycleGuard | None,
        threading.RLock | None,
    ]:
        """获取用户和会话的并发控制资源"""
        with self._resource_lock:
            user_lock = self._user_locks.setdefault(user_id, asyncio.Lock())
            user_guard = self._user_guards.setdefault(user_id, _LifecycleGuard())
            start_lock = self._start_locks.setdefault(user_id, threading.Lock())
            conversation_guard = None
            mutation_lock = None
            if conversation_id is not None:
                conversation_guard = self._conversation_guards.setdefault(
                    (user_id, conversation_id),
                    _LifecycleGuard(),
                )
                mutation_lock = self._mutation_locks.setdefault(
                    (user_id, conversation_id),
                    threading.RLock(),
                )
            return (
                user_lock,
                user_guard,
                start_lock,
                conversation_guard,
                mutation_lock,
            )

    def _touch_user(self, user_id: int) -> None:
        """记录用户沙盒最近活动时间"""
        with self._activity_lock:
            self._last_activity[user_id] = time.time()
        self._capacity.notify_waiters()

    def _last_activity_timestamp(self, user_id: int) -> float:
        """获取用户最近活动时间戳"""
        with self._activity_lock:
            return self._last_activity.get(user_id, 0.0)

    def _idle_seconds(self, user_id: int) -> float:
        """获取用户沙盒持续空闲的秒数"""
        with self._activity_lock:
            last_activity = self._last_activity.get(user_id)
        if last_activity is None:
            return 0.0
        return max(0.0, time.time() - last_activity)

    def _container_name(self, user_id: int) -> str:
        """构造用户容器名称"""
        return f"dataagent-{self._config.deployment_namespace}-sandbox-user-{user_id}"

    def _volume_name(self, user_id: int) -> str:
        """构造用户数据卷名称"""
        return f"{self._container_name(user_id)}-data"

    def _resource_labels(self, user_id: int) -> dict[str, str]:
        """构造容器和卷的归属标签"""
        return {
            _DEPLOYMENT_LABEL: self._config.deployment_namespace,
            _USER_LABEL: str(user_id),
            _QUOTA_MODE_LABEL: self._config.workspace_quota_mode,
            _QUOTA_BYTES_LABEL: str(self._config.max_workspace_bytes),
        }

    def _container_filters(self) -> dict[str, str | list[str] | bool]:
        """构造当前部署实例的 Docker 资源过滤条件"""
        return {
            "label": [
                f"{_DEPLOYMENT_LABEL}={self._config.deployment_namespace}",
                _USER_LABEL,
            ]
        }

    def _volume_driver_options(self, user_id: int) -> dict[str, str]:
        """渲染用户卷驱动参数"""
        fields = {
            "deployment_namespace": self._config.deployment_namespace,
            "user_id": user_id,
            "max_workspace_bytes": self._config.max_workspace_bytes,
        }
        return {
            key: value.format_map(fields)
            for key, value in self._config.volume_driver_options.items()
        }

    def _runtime_container_spec(self) -> dict[str, Any]:
        """返回创建容器使用的完整运行规格"""
        return {
            "command": ["sleep", "infinity"],
            "init": True,
            "read_only": True,
            "user": "1000:1000",
            "working_dir": "/workspace",
            "tmpfs": {"/tmp": "rw,nosuid,nodev,size=256m"},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": self._config.memory_limit,
            "nano_cpus": self._config.nano_cpus,
            "pids_limit": self._config.pids_limit,
            "network_mode": self._config.network_mode,
            "environment": {"HOME": "/tmp"},
        }

    def _container_spec_digest(self, image_id: str) -> str:
        """计算完整容器运行和存储规格的稳定摘要"""
        spec_payload = {
            "layout_version": 4,
            "image_id": image_id,
            "runtime": self._runtime_container_spec(),
            "workspace_mount": {
                "target": "/workspace",
                "mode": "rw",
            },
            "volume": {
                "driver": self._config.volume_driver,
                "driver_options": self._config.volume_driver_options,
                "quota_mode": self._config.workspace_quota_mode,
                "quota_bytes": self._config.max_workspace_bytes,
            },
        }
        return hashlib.sha256(
            json.dumps(spec_payload, sort_keys=True).encode()
        ).hexdigest()

    def _get_or_create_volume(self, user_id: int):
        """获取用户数据卷并校验归属"""
        client = self._get_client()
        volume_name = self._volume_name(user_id)
        try:
            volume = client.volumes.get(volume_name)
        except NotFound:
            return client.volumes.create(
                name=volume_name,
                driver=self._config.volume_driver,
                driver_opts=self._volume_driver_options(user_id),
                labels=self._resource_labels(user_id),
            )
        volume.reload()
        expected_labels = self._resource_labels(user_id)
        actual_labels = volume.attrs.get("Labels") or {}
        if any(
            actual_labels.get(key) != value for key, value in expected_labels.items()
        ):
            raise RuntimeError(f"Docker 数据卷名称已被占用: {volume_name}")
        actual_driver = volume.attrs.get("Driver")
        actual_options = volume.attrs.get("Options") or {}
        expected_options = self._volume_driver_options(user_id)
        if (
            actual_driver != self._config.volume_driver
            or actual_options != expected_options
        ):
            raise RuntimeError(
                f"Docker 数据卷存储策略发生变更，需要迁移: {volume_name}"
            )
        return volume

    def _create_container(self, user_id: int) -> Container:
        """创建保持停止状态的用户容器"""
        client = self._get_client()
        volume = self._get_or_create_volume(user_id)
        if self._container_spec is None:
            raise RuntimeError("Docker 沙箱容器配置不可用")

        container = client.containers.create(
            self._config.image,
            name=self._container_name(user_id),
            volumes={volume.name: {"bind": "/workspace", "mode": "rw"}},
            labels={
                **self._resource_labels(user_id),
                _CONTAINER_SPEC_LABEL: self._container_spec,
            },
            **self._runtime_container_spec(),
        )
        logger.info(f"创建已停止的用户 Docker 沙箱: user_id={user_id}")
        return container

    def _get_or_create_storage_container_sync(self, user_id: int) -> Container:
        """获取或创建容器，但不启动容器"""
        if user_id < 0:
            raise ValueError("user_id 不能为负数")
        name = self._container_name(user_id)
        container = self._get_existing_container_sync(user_id)
        if container is not None:
            if container.labels.get(_CONTAINER_SPEC_LABEL) == self._container_spec:
                return container
            logger.info(f"重建过期的 Docker 沙箱: user_id={user_id}")
            container.remove(force=True)
            self._mark_user_not_running(user_id)
        try:
            return self._create_container(user_id)
        except APIError as exc:
            if exc.status_code != 409:
                raise
            existing_container = self._get_existing_container_sync(user_id)
            if existing_container is None:
                raise RuntimeError(f"Docker container creation raced: {name}") from exc
            return existing_container

    def _get_existing_container_sync(self, user_id: int) -> Container | None:
        """获取已存在的用户容器"""
        name = self._container_name(user_id)
        try:
            container = self._get_client().containers.get(name)
        except NotFound:
            return None
        container.reload()
        expected_labels = self._resource_labels(user_id)
        if any(
            container.labels.get(key) != value for key, value in expected_labels.items()
        ):
            raise RuntimeError(f"Docker container name is already in use: {name}")
        return container

    def _mark_user_not_running(self, user_id: int) -> None:
        """释放用户占用的全局运行槽位"""
        self._capacity.mark_not_running(user_id)

    def _complete_running_reservation(
        self,
        user_id: int,
        *,
        running: bool,
    ) -> None:
        """完成或回滚运行槽位预留"""
        self._capacity.complete_reservation(user_id, running=running)

    def _try_evict_idle_user_sync(self, user_id: int) -> bool:
        """尝试停止一个没有活跃操作的用户容器"""
        with self._resource_lock:
            user_guard = self._user_guards.setdefault(user_id, _LifecycleGuard())
            start_lock = self._start_locks.setdefault(user_id, threading.Lock())
        with user_guard.try_maintenance() as acquired:
            if not acquired:
                return False
            with start_lock:
                container = self._get_existing_container_sync(user_id)
                if container is not None and container.status == "running":
                    container.stop(timeout=10)
                    logger.info(
                        f"因容量限制停止最久未使用的 Docker 沙箱: user_id={user_id}"
                    )
                self._mark_user_not_running(user_id)
                return True

    def _reserve_running_slot_sync(
        self,
        user_id: int,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """等待并预留一个全局运行容器槽位"""
        return self._capacity.acquire(
            user_id,
            self._last_activity_timestamp,
            self._try_evict_idle_user_sync,
            cancel_event,
        )

    def _get_running_container_sync(
        self,
        user_id: int,
        start_lock: threading.Lock,
        cancel_event: threading.Event | None = None,
    ) -> Container:
        """获取用户容器并在取得全局槽位后按需启动"""
        with start_lock:
            container = self._get_or_create_storage_container_sync(user_id)
            container.reload()
            reserved = self._reserve_running_slot_sync(user_id, cancel_event)
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise SandboxCapacityCancelledError(
                        "Docker 沙箱启动已取消"
                    )
                if container.status != "running":
                    container.start()
                    container.reload()
                    logger.info(f"启动 Docker 沙箱: user_id={user_id}")
            except Exception:
                if reserved:
                    self._complete_running_reservation(user_id, running=False)
                raise
            if reserved:
                self._complete_running_reservation(user_id, running=True)
            else:
                self._capacity.mark_running(user_id)
            return container

    def _reconcile_running_containers_sync(self) -> None:
        """启动时登记已有容器并收敛到运行上限"""
        containers = self._get_client().containers.list(
            all=True,
            filters=self._container_filters(),
        )
        running: list[tuple[int, Container]] = []
        for container in containers:
            raw_user_id = container.labels.get(_USER_LABEL)
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            with self._resource_lock:
                self._user_guards.setdefault(user_id, _LifecycleGuard())
                self._start_locks.setdefault(user_id, threading.Lock())
            container.reload()
            activity_at = self._recover_activity_timestamp_sync(container)
            with self._activity_lock:
                self._last_activity.setdefault(user_id, activity_at)
                self._last_persisted_activity.setdefault(user_id, activity_at)
            if container.status == "running":
                running.append((user_id, container))

        running.sort(
            key=lambda item: self._last_activity_timestamp(item[0]), reverse=True
        )
        overflow_user_ids = set(
            self._capacity.reconcile([user_id for user_id, _ in running])
        )
        for user_id, container in running:
            if user_id not in overflow_user_ids:
                continue
            container.stop(timeout=10)
            logger.info(f"启动时停止超出上限的 Docker 沙箱: user_id={user_id}")

    @contextmanager
    def _open_archive_sync(
        self,
        container: Container,
        path: str,
    ) -> Generator[tarfile.TarFile, None, None]:
        """流式打开容器中的 archive，适用于运行或停止状态"""
        chunks, _ = container.get_archive(path)
        raw_reader = _IteratorReader(iter(chunks))
        buffered_reader = io.BufferedReader(raw_reader)
        try:
            with tarfile.open(fileobj=buffered_reader, mode="r|*") as archive:
                yield archive
        finally:
            buffered_reader.close()

    def _inspect_archive_path_sync(
        self,
        container: Container,
        path: str,
    ) -> tarfile.TarInfo | None:
        """读取容器路径对应的首个 archive 条目"""
        try:
            with self._open_archive_sync(container, path) as archive:
                member = next(iter(archive), None)
                if member is None:
                    return None
                return member
        except NotFound:
            return None

    def _read_archive_file_sync(
        self,
        container: Container,
        path: str,
        max_bytes: int,
    ) -> tuple[bytes, tarfile.TarInfo]:
        """从运行或停止容器读取一个普通文件"""
        with self._open_archive_sync(container, path) as archive:
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

    @staticmethod
    def _parse_docker_timestamp(value: object) -> float:
        """解析 Docker 返回的 RFC3339 时间戳"""
        if not isinstance(value, str) or not value or value.startswith("0001-"):
            return 0.0
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.timestamp()
        except ValueError:
            return 0.0

    def _recover_activity_timestamp_sync(self, container: Container) -> float:
        """从持久文件或 Docker 状态恢复最近活动时间"""
        now = time.time()
        try:
            content, member = self._read_archive_file_sync(
                container,
                _SANDBOX_ACTIVITY_FILE,
                16 * 1024,
            )
            if member.uid != 0:
                raise RuntimeError("沙箱活动记录文件拥有者无效")
            payload = json.loads(content)
            if payload.get("version") != _ACTIVITY_FILE_VERSION:
                raise RuntimeError("不支持的沙箱活动记录文件版本")
            activity_at = float(payload["last_activity_at"])
            if 0 < activity_at <= now + 300:
                return activity_at
            raise RuntimeError("沙箱活动记录文件包含无效的时间戳")
        except (NotFound, FileNotFoundError):
            pass

        state = container.attrs.get("State") or {}
        candidates = [
            self._parse_docker_timestamp(state.get("StartedAt")),
            self._parse_docker_timestamp(state.get("FinishedAt")),
            self._parse_docker_timestamp(container.attrs.get("Created")),
        ]
        recovered = max(candidates, default=0.0)
        return min(recovered, now) if recovered > 0 else now

    def _persist_activity_sync(
        self,
        user_id: int,
        container: Container,
        *,
        force: bool = False,
    ) -> None:
        """将内存活动时间写入用户持久卷"""
        with self._activity_lock:
            activity_at = self._last_activity.get(user_id)
            persisted_at = self._last_persisted_activity.get(user_id, 0.0)
        if activity_at is None or not force and activity_at <= persisted_at:
            return
        content = json.dumps(
            {
                "version": _ACTIVITY_FILE_VERSION,
                "last_activity_at": activity_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self._put_archive_sync(
            container,
            "/workspace",
            [],
            [
                (
                    PurePosixPath(_SANDBOX_ACTIVITY_FILE).name,
                    0,
                    0,
                    0o600,
                    io.BytesIO(content),
                    len(content),
                )
            ],
        )
        with self._activity_lock:
            self._last_persisted_activity[user_id] = activity_at

    def _put_archive_sync(
        self,
        container: Container,
        base_path: str,
        directories: list[tuple[str, int, int, int]],
        files: list[tuple[str, int, int, int, BinaryIO, int]],
    ) -> None:
        """构造受控 tar 并写入运行或停止容器"""
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
                raise OSError(f"Failed to write Docker archive: {base_path}")

    def _write_uid_registry_sync(
        self,
        container: Container,
        registry: _SandboxUidRegistry,
    ) -> None:
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
        self._put_archive_sync(
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

    def _scan_workspace_uids_sync(
        self,
        container: Container,
    ) -> _SandboxUidRegistry:
        """首次迁移时从目录属主构建完整会话 UID 注册表"""
        mapping: dict[str, int] = {}
        try:
            with self._open_archive_sync(container, _SANDBOX_WORKSPACE_ROOT) as archive:
                for member in archive:
                    if not member.isdir():
                        continue
                    parts = PurePosixPath(member.name).parts
                    if "conversations" in parts:
                        parts = parts[parts.index("conversations") + 1 :]
                    if len(parts) != 1:
                        continue
                    try:
                        conversation_id = str(UUID(parts[0]))
                    except ValueError:
                        continue
                    if _MIN_CONVERSATION_UID <= member.uid <= _MAX_CONVERSATION_UID:
                        mapping[conversation_id] = member.uid
        except NotFound:
            pass
        return _SandboxUidRegistry(conversations=mapping, sessions={})

    @staticmethod
    def _validate_session_registry_key(key: str) -> str:
        """校验并规范化 UID 注册表中的 Session 键"""
        parts = PurePosixPath(key).parts
        if len(parts) != 6 or parts[1] != "analyses" or parts[3] != "sessions":
            raise ValueError("Invalid sandbox Session UID key")
        conversation_id = str(UUID(parts[0]))
        scope = SandboxSessionScope(parts[2], parts[4], parts[5])
        return scope.registry_key(UUID(conversation_id))

    @staticmethod
    def _validate_uid_registry(registry: _SandboxUidRegistry) -> None:
        """校验 conversation 和 Session UID 全局唯一"""
        values = [*registry.conversations.values(), *registry.sessions.values()]
        if len(values) != len(set(values)):
            raise RuntimeError("沙箱 UID 注册表包含重复的 UID")
        if any(
            uid < _MIN_CONVERSATION_UID or uid > _MAX_CONVERSATION_UID for uid in values
        ):
            raise RuntimeError("沙箱 UID 注册表包含无效的 UID")

    def _load_uid_registry_sync(
        self,
        container: Container,
    ) -> _SandboxUidRegistry:
        """读取 UID 注册表，不存在时从已有工作区迁移"""
        migrated = False
        try:
            content, member = self._read_archive_file_sync(
                container,
                _SANDBOX_UID_REGISTRY,
                4 * 1024 * 1024,
            )
            if member.uid != 0:
                raise RuntimeError("沙箱 UID 注册表文件拥有者无效")
            payload = json.loads(content)
            version = payload.get("version")
            if version not in {1, _UID_REGISTRY_VERSION}:
                raise RuntimeError("不支持的沙箱 UID 注册表版本")
            raw_conversations = payload.get("conversations")
            raw_sessions = payload.get("sessions", {}) if version == 2 else {}
            if not isinstance(raw_conversations, dict) or not isinstance(
                raw_sessions,
                dict,
            ):
                raise TypeError("沙箱 UID 注册表格式无效")
            registry = _SandboxUidRegistry(
                conversations={
                    str(UUID(key)): int(value)
                    for key, value in raw_conversations.items()
                },
                sessions={
                    self._validate_session_registry_key(key): int(value)
                    for key, value in raw_sessions.items()
                },
            )
            migrated = version == 1
        except (NotFound, FileNotFoundError):
            registry = self._scan_workspace_uids_sync(container)
            migrated = True
        self._validate_uid_registry(registry)
        if migrated:
            self._write_uid_registry_sync(container, registry)
        return registry

    def _allocate_conversation_uid(
        self,
        conversation_id: UUID,
        used_uids: set[int],
    ) -> int:
        """为会话确定性分配未使用的 Linux UID"""
        uid_range = _MAX_CONVERSATION_UID - _MIN_CONVERSATION_UID + 1
        for attempt in range(uid_range):
            digest = hashlib.blake2s(
                conversation_id.bytes + attempt.to_bytes(8, "big")
            ).digest()
            candidate = (
                _MIN_CONVERSATION_UID + int.from_bytes(digest[:8], "big") % uid_range
            )
            if candidate not in used_uids:
                return candidate
        raise RuntimeError("conversation uid range exhausted")

    def _allocate_session_uid(
        self,
        registry_key: str,
        used_uids: set[int],
    ) -> int:
        """为 Agent Session 确定性分配未使用的 Linux UID"""
        uid_range = _MAX_CONVERSATION_UID - _MIN_CONVERSATION_UID + 1
        seed = f"session:{registry_key}".encode()
        for attempt in range(uid_range):
            digest = hashlib.blake2s(seed + attempt.to_bytes(8, "big")).digest()
            candidate = (
                _MIN_CONVERSATION_UID + int.from_bytes(digest[:8], "big") % uid_range
            )
            if candidate not in used_uids:
                return candidate
        raise RuntimeError("sandbox uid range exhausted")

    def _ensure_workspace_archive_sync(
        self,
        container: Container,
        conversation_id: UUID,
    ) -> int:
        """不启动容器地创建会话工作区并返回稳定 UID"""
        self._put_archive_sync(
            container,
            "/workspace",
            [
                ("conversations", 0, 0, 0o711),
                (PurePosixPath(_SANDBOX_STAGING_ROOT).name, 0, 0, 0o700),
            ],
            [],
        )
        registry = self._load_uid_registry_sync(container)
        key = str(conversation_id)
        conversation_uid = registry.conversations.get(key)
        if conversation_uid is None:
            conversation_uid = self._allocate_conversation_uid(
                conversation_id,
                {
                    *registry.conversations.values(),
                    *registry.sessions.values(),
                },
            )
            registry.conversations[key] = conversation_uid
            self._write_uid_registry_sync(container, registry)

        target_path = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        existing = self._inspect_archive_path_sync(container, target_path)
        if existing is not None and (
            not existing.isdir() or existing.uid != conversation_uid
        ):
            raise RuntimeError(
                "Conversation workspace owner does not match UID registry"
            )

        conversation_name = str(conversation_id)
        self._put_archive_sync(
            container,
            _SANDBOX_WORKSPACE_ROOT,
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
        self._put_archive_sync(
            container,
            _SANDBOX_STAGING_ROOT,
            [
                (conversation_name, 0, 0, 0o700),
                (f"{conversation_name}/{conversation_uid}", 0, 0, 0o700),
            ],
            [],
        )
        return conversation_uid

    def _ensure_session_workspace_archive_sync(
        self,
        container: Container,
        conversation_id: UUID,
        scope: SandboxSessionScope,
    ) -> tuple[int, int]:
        """创建 Agent Session 目录并返回 conversation/session UID"""
        conversation_uid = self._ensure_workspace_archive_sync(
            container,
            conversation_id,
        )
        registry = self._load_uid_registry_sync(container)
        registry_key = scope.registry_key(conversation_id)
        session_uid = registry.sessions.get(registry_key)
        if session_uid is None:
            session_uid = self._allocate_session_uid(
                registry_key,
                {
                    *registry.conversations.values(),
                    *registry.sessions.values(),
                },
            )
            registry.sessions[registry_key] = session_uid
            self._write_uid_registry_sync(container, registry)

        conversation_name = str(conversation_id)
        session_relative = scope.relative_workspace
        session_path = posixpath.join(
            _SANDBOX_WORKSPACE_ROOT,
            conversation_name,
            session_relative,
        )
        existing = self._inspect_archive_path_sync(container, session_path)
        if existing is not None and (
            not existing.isdir()
            or existing.uid not in {conversation_uid, session_uid}
            or existing.gid != conversation_uid
        ):
            raise RuntimeError("Agent Session workspace owner is invalid")

        analysis_root = f"analyses/{scope.analysis_id}"
        sessions_root = f"{analysis_root}/sessions"
        agent_root = f"{sessions_root}/{scope.agent_type}"
        self._put_archive_sync(
            container,
            f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_name}",
            [
                ("analyses", conversation_uid, conversation_uid, 0o750),
                (analysis_root, conversation_uid, conversation_uid, 0o750),
                (sessions_root, conversation_uid, conversation_uid, 0o750),
                (agent_root, conversation_uid, conversation_uid, 0o750),
                (session_relative, session_uid, conversation_uid, 0o750),
                (
                    f"{session_relative}/.home",
                    session_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{session_relative}/.cache",
                    session_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{session_relative}/.cache/uv",
                    session_uid,
                    conversation_uid,
                    0o700,
                ),
                (
                    f"{session_relative}/.tmp",
                    session_uid,
                    conversation_uid,
                    0o700,
                ),
            ],
            [],
        )
        self._put_archive_sync(
            container,
            posixpath.join(_SANDBOX_STAGING_ROOT, conversation_name),
            [(str(session_uid), 0, 0, 0o700)],
            [],
        )
        prepared = self._inspect_archive_path_sync(container, session_path)
        if (
            prepared is None
            or not prepared.isdir()
            or prepared.uid != session_uid
            or prepared.gid != conversation_uid
            or prepared.mode & 0o777 != 0o750
        ):
            raise RuntimeError("Agent Session workspace permission setup failed")
        return conversation_uid, session_uid

    async def get_backend(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> DockerSandboxBackend:
        """获取用户指定会话的沙盒后端"""
        await self.init()
        (
            user_lock,
            user_guard,
            start_lock,
            conversation_guard,
            mutation_lock,
        ) = await self._get_resources(user_id, conversation_id)
        if conversation_guard is None or mutation_lock is None:
            raise RuntimeError("Conversation sandbox guard is unavailable")

        def prepare() -> int:
            with user_guard.maintenance(), conversation_guard.maintenance():
                container = self._get_or_create_storage_container_sync(user_id)
                return self._ensure_workspace_archive_sync(
                    container,
                    conversation_id,
                )

        async with user_lock:
            conversation_uid = await asyncio.to_thread(prepare)
        self._touch_user(user_id)
        backend = DockerSandboxBackend(
            user_id,
            conversation_id,
            conversation_uid,
            self._config,
            user_guard,
            conversation_guard,
            mutation_lock,
            lambda: self._touch_user(user_id),
            lambda cancel_event: self._get_running_container_sync(
                user_id,
                start_lock,
                cancel_event,
            ),
            self._capacity.notify_waiters,
        )
        return backend

    async def get_session_backend(
        self,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        agent_type: str,
        session_id: str,
    ) -> DockerSandboxBackend:
        """获取独立 Linux 身份的专业 Agent Session 后端"""
        await self.init()
        scope = SandboxSessionScope(analysis_id, agent_type, session_id)
        (
            user_lock,
            user_guard,
            start_lock,
            conversation_guard,
            mutation_lock,
        ) = await self._get_resources(user_id, conversation_id)
        if conversation_guard is None or mutation_lock is None:
            raise RuntimeError("Conversation sandbox guard is unavailable")

        def prepare() -> tuple[int, int]:
            with user_guard.maintenance(), conversation_guard.maintenance():
                container = self._get_or_create_storage_container_sync(user_id)
                return self._ensure_session_workspace_archive_sync(
                    container,
                    conversation_id,
                    scope,
                )

        async with user_lock:
            conversation_uid, session_uid = await asyncio.to_thread(prepare)
        self._touch_user(user_id)
        return DockerSandboxBackend(
            user_id,
            conversation_id,
            conversation_uid,
            self._config,
            user_guard,
            conversation_guard,
            mutation_lock,
            lambda: self._touch_user(user_id),
            lambda cancel_event: self._get_running_container_sync(
                user_id,
                start_lock,
                cancel_event,
            ),
            self._capacity.notify_waiters,
            session_scope=scope,
            execution_uid=session_uid,
        )

    @staticmethod
    def _registered_session_uid_for_path(
        registry: _SandboxUidRegistry,
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

    @classmethod
    def _allowed_file_uids_for_path(
        cls,
        registry: _SandboxUidRegistry,
        conversation_id: UUID,
        conversation_uid: int,
        relative_path: str,
    ) -> set[int]:
        """返回给定会话文件路径允许使用的属主 UID"""
        allowed = {conversation_uid}
        session_uid = cls._registered_session_uid_for_path(
            registry,
            conversation_id,
            relative_path,
        )
        if session_uid is not None:
            allowed.add(session_uid)
        return allowed

    def _validate_attachment_target_sync(
        self,
        container: Container,
        conversation_id: UUID,
        conversation_uid: int,
        relative_path: str,
    ) -> tuple[list[tuple[str, int, int, int]], int]:
        """校验附件路径中的每个组件并返回待创建目录和被替换大小"""
        workspace = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        registry = self._load_uid_registry_sync(container)
        session_uid = self._registered_session_uid_for_path(
            registry,
            conversation_id,
            relative_path,
        )
        root_info = self._inspect_archive_path_sync(container, workspace)
        if (
            root_info is None
            or not root_info.isdir()
            or root_info.uid != conversation_uid
            or root_info.gid != conversation_uid
        ):
            raise OSError("Invalid conversation workspace")

        parts = PurePosixPath(relative_path).parts
        directories: list[tuple[str, int, int, int]] = []
        current_path = workspace
        for index, component in enumerate(parts[:-1], start=1):
            current_path = posixpath.join(current_path, component)
            info = self._inspect_archive_path_sync(container, current_path)
            directory_uid = (
                session_uid
                if session_uid is not None and index >= 5
                else conversation_uid
            )
            if info is None:
                directories.append(
                    (
                        "/".join(parts[:index]),
                        directory_uid,
                        conversation_uid,
                        0o750,
                    )
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

        target_path = posixpath.join(workspace, relative_path)
        target_info = self._inspect_archive_path_sync(container, target_path)
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

    def _workspace_archive_size_sync(
        self,
        container: Container,
        conversation_id: UUID,
        conversation_uid: int,
    ) -> int:
        """在停止状态下流式统计会话工作区普通文件大小"""
        workspace = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
        registry = self._load_uid_registry_sync(container)
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
        with self._open_archive_sync(container, workspace) as archive:
            for member in archive:
                if member.isreg():
                    if member.uid not in allowed_uids or member.gid != conversation_uid:
                        raise OSError("Conversation workspace contains invalid owner")
                    total += member.size
                    if total > self._config.max_workspace_bytes:
                        break
        return total

    def _upload_attachment_sync(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        content: BinaryIO,
        user_guard: _LifecycleGuard,
        conversation_guard: _LifecycleGuard,
        mutation_lock: threading.RLock,
    ) -> None:
        """使用 Docker Archive API 上传附件，不启动容器"""
        with (
            user_guard.maintenance(),
            conversation_guard.maintenance(),
            mutation_lock,
        ):
            container = self._get_or_create_storage_container_sync(user_id)
            conversation_uid = self._ensure_workspace_archive_sync(
                container,
                conversation_id,
            )
            content.seek(0, io.SEEK_END)
            size = content.tell()
            content.seek(0)
            if size > self._config.max_file_bytes:
                raise SandboxFileTooLargeError(
                    f"file too large: {size} > {self._config.max_file_bytes}"
                )
            directories, replaced_size = self._validate_attachment_target_sync(
                container,
                conversation_id,
                conversation_uid,
                normalized_path,
            )
            current_size = self._workspace_archive_size_sync(
                container,
                conversation_id,
                conversation_uid,
            )
            projected_size = current_size - replaced_size + size
            if projected_size > self._config.max_workspace_bytes:
                raise SandboxStorageLimitError(
                    "workspace limit exceeded: "
                    f"{projected_size} > {self._config.max_workspace_bytes}"
                )
            workspace = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
            self._put_archive_sync(
                container,
                workspace,
                directories,
                [
                    (
                        normalized_path,
                        conversation_uid,
                        conversation_uid,
                        0o640,
                        content,
                        size,
                    )
                ],
            )
            written = self._inspect_archive_path_sync(
                container,
                posixpath.join(workspace, normalized_path),
            )
            if (
                written is None
                or not written.isreg()
                or written.uid != conversation_uid
                or written.size != size
            ):
                raise OSError("Uploaded attachment failed validation")

    def _download_attachment_sync(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        user_guard: _LifecycleGuard,
        conversation_guard: _LifecycleGuard,
    ) -> bytes:
        """使用 Docker Archive API 下载附件，不启动容器"""
        with user_guard.maintenance(), conversation_guard.maintenance():
            container = self._get_or_create_storage_container_sync(user_id)
            conversation_uid = self._ensure_workspace_archive_sync(
                container,
                conversation_id,
            )
            self._validate_attachment_target_sync(
                container,
                conversation_id,
                conversation_uid,
                normalized_path,
            )
            workspace = f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}"
            content, member = self._read_archive_file_sync(
                container,
                posixpath.join(workspace, normalized_path),
                self._config.max_file_bytes,
            )
            registry = self._load_uid_registry_sync(container)
            allowed_uids = self._allowed_file_uids_for_path(
                registry,
                conversation_id,
                conversation_uid,
                normalized_path,
            )
            if member.uid not in allowed_uids or member.gid != conversation_uid:
                raise FileNotFoundError(normalized_path)
            return content

    async def _upload_normalized_file(
        self,
        user_id: int,
        conversation_id: UUID,
        normalized_path: str,
        content: BinaryIO,
    ) -> None:
        """将已校验路径的文件对象写入用户会话目录"""
        await self.init()
        (
            user_lock,
            user_guard,
            _,
            conversation_guard,
            mutation_lock,
        ) = await self._get_resources(user_id, conversation_id)
        if conversation_guard is None or mutation_lock is None:
            raise RuntimeError("Conversation sandbox guard is unavailable")
        async with user_lock:
            await asyncio.to_thread(
                self._upload_attachment_sync,
                user_id,
                conversation_id,
                normalized_path,
                content,
                user_guard,
                conversation_guard,
                mutation_lock,
            )
        self._touch_user(user_id)

    async def write_artifact(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> None:
        """写入可信系统分析产物"""
        await self._upload_normalized_file(
            user_id,
            conversation_id,
            normalize_attachment_path(path),
            content,
        )

    async def upload_user_attachment(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> str:
        """上传用户可变附件并返回规范化路径"""
        normalized_path = normalize_user_attachment_path(path)
        await self._upload_normalized_file(
            user_id,
            conversation_id,
            normalized_path,
            content,
        )
        return normalized_path

    async def download_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bytes:
        """下载用户会话目录中的文件"""
        normalized_path = normalize_attachment_path(path)
        await self.init()
        user_lock, user_guard, _, conversation_guard, _ = await self._get_resources(
            user_id,
            conversation_id,
        )
        if conversation_guard is None:
            raise RuntimeError("Conversation sandbox guard is unavailable")
        async with user_lock:
            try:
                content = await asyncio.to_thread(
                    self._download_attachment_sync,
                    user_id,
                    conversation_id,
                    normalized_path,
                    user_guard,
                    conversation_guard,
                )
            except NotFound:
                raise FileNotFoundError(normalized_path) from None
        self._touch_user(user_id)
        return content

    async def delete_user_attachment(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> None:
        """删除用户可变附件"""
        normalized_path = normalize_user_attachment_path(path)
        backend = await self.get_backend(user_id, conversation_id)
        if not await asyncio.to_thread(backend.is_file, normalized_path):
            return
        result = await backend.adelete(normalized_path)
        if result.error and "not found" not in result.error:
            raise OSError(result.error)

    async def is_file(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
    ) -> bool:
        """检查用户会话目录中的文件是否存在"""
        normalized_path = normalize_attachment_path(path)
        await self.init()
        user_lock, user_guard, _, conversation_guard, _ = await self._get_resources(
            user_id,
            conversation_id,
        )
        if conversation_guard is None:
            return False

        def inspect() -> bool:
            with user_guard.maintenance(), conversation_guard.maintenance():
                container = self._get_or_create_storage_container_sync(user_id)
                conversation_uid = self._ensure_workspace_archive_sync(
                    container,
                    conversation_id,
                )
                try:
                    self._validate_attachment_target_sync(
                        container,
                        conversation_id,
                        conversation_uid,
                        normalized_path,
                    )
                except SandboxPathError:
                    return False
                target = self._inspect_archive_path_sync(
                    container,
                    posixpath.join(
                        _SANDBOX_WORKSPACE_ROOT,
                        str(conversation_id),
                        normalized_path,
                    ),
                )
                registry = self._load_uid_registry_sync(container)
                try:
                    allowed_uids = self._allowed_file_uids_for_path(
                        registry,
                        conversation_id,
                        conversation_uid,
                        normalized_path,
                    )
                except SandboxPathError:
                    return False
                return bool(
                    target is not None
                    and target.isreg()
                    and target.uid in allowed_uids
                    and target.gid == conversation_uid
                )

        async with user_lock:
            result = await asyncio.to_thread(inspect)
        self._touch_user(user_id)
        return result

    async def delete_conversation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """删除用户沙盒中的会话目录"""
        await self.init()
        (
            user_lock,
            user_guard,
            start_lock,
            conversation_guard,
            mutation_lock,
        ) = await self._get_resources(user_id, conversation_id)
        if conversation_guard is None or mutation_lock is None:
            return

        def delete() -> None:
            with (
                user_guard.maintenance(),
                conversation_guard.maintenance(allow_deleted=True),
                mutation_lock,
            ):
                container = self._get_running_container_sync(user_id, start_lock)
                self._ensure_workspace_archive_sync(container, conversation_id)
                result = container.exec_run(
                    [
                        "rm",
                        "-rf",
                        "--",
                        f"{_SANDBOX_WORKSPACE_ROOT}/{conversation_id}",
                        posixpath.join(
                            _SANDBOX_STAGING_ROOT,
                            str(conversation_id),
                        ),
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
                    raise OSError(detail or "failed to delete conversation sandbox")
                registry = self._load_uid_registry_sync(container)
                registry.conversations.pop(str(conversation_id), None)
                session_prefix = f"{conversation_id}/"
                registry.sessions = {
                    key: value
                    for key, value in registry.sessions.items()
                    if not key.startswith(session_prefix)
                }
                self._write_uid_registry_sync(container, registry)

        async with user_lock:
            conversation_guard.mark_deleted()
            await asyncio.to_thread(delete)
        self._touch_user(user_id)

    async def delete_user_sandbox(self, user_id: int) -> None:
        """删除用户容器及其持久化数据卷"""
        await self.init()
        user_lock, user_guard, _, _, _ = await self._get_resources(user_id)

        def delete() -> None:
            with user_guard.maintenance(allow_deleted=True):
                client = self._get_client()
                try:
                    client.containers.get(self._container_name(user_id)).remove(
                        force=True
                    )
                except NotFound:
                    pass
                try:
                    client.volumes.get(self._volume_name(user_id)).remove(force=True)
                except NotFound:
                    pass

        async with user_lock:
            user_guard.mark_deleted()
            self._capacity.cancel_user(user_id)
            await asyncio.to_thread(delete)
        self._mark_user_not_running(user_id)
        with self._resource_lock:
            self._user_locks.pop(user_id, None)
            self._user_guards.pop(user_id, None)
            self._start_locks.pop(user_id, None)
            for key in [key for key in self._conversation_guards if key[0] == user_id]:
                self._conversation_guards.pop(key, None)
                self._mutation_locks.pop(key, None)
        with self._activity_lock:
            self._last_activity.pop(user_id, None)
            self._last_persisted_activity.pop(user_id, None)

    def health(self) -> DockerSandboxHealth:
        """返回可供健康检查和监控采集的状态快照"""
        cleanup_task = self._cleanup_task
        with self._health_lock:
            return DockerSandboxHealth(
                cleanup_task_running=bool(
                    cleanup_task is not None and not cleanup_task.done()
                ),
                last_cleanup_started_at=self._last_cleanup_started_at,
                last_cleanup_completed_at=self._last_cleanup_completed_at,
                cleanup_consecutive_failures=self._cleanup_consecutive_failures,
                cleanup_last_error=self._cleanup_last_error,
                quota_mode=self._config.workspace_quota_mode,
                capacity=self._capacity.snapshot(),
            )

    def _record_cleanup_result(
        self,
        started_at: float,
        errors: list[str],
    ) -> None:
        """记录一次清理周期的健康状态"""
        with self._health_lock:
            self._last_cleanup_started_at = started_at
            self._last_cleanup_completed_at = time.time()
            if errors:
                self._cleanup_consecutive_failures += 1
                self._cleanup_last_error = errors[-1]
                failures = self._cleanup_consecutive_failures
            else:
                self._cleanup_consecutive_failures = 0
                self._cleanup_last_error = None
                failures = 0
        if failures >= self._config.cleanup_failure_alert_threshold:
            logger.error(
                f"Docker 沙箱清理连续失败: consecutive_failures={failures}, last_error={errors[-1]}"
            )

    async def _run_cleanup_cycle(self) -> None:
        """执行一个带用户级错误隔离的清理周期"""
        started_at = time.time()
        errors: list[str] = []
        try:
            with self._activity_lock:
                user_ids = set(self._last_activity)
            user_ids.update(await asyncio.to_thread(self._managed_user_ids_sync))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append(f"resource discovery failed: {exc}")
            logger.exception("发现 Docker 沙箱资源失败")
            self._record_cleanup_result(started_at, errors)
            return

        for user_id in user_ids:
            try:
                (
                    user_lock,
                    user_guard,
                    start_lock,
                    _,
                    _,
                ) = await self._get_resources(user_id)
                async with user_lock:
                    await asyncio.to_thread(
                        self._cleanup_idle_container_sync,
                        user_id,
                        user_guard,
                        start_lock,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"user_id={user_id}: {exc}")
                logger.exception(f"清理 Docker 沙箱失败: user_id={user_id}")
        self._record_cleanup_result(started_at, errors)

    async def _cleanup_idle_containers(self) -> None:
        """定期停止或删除空闲容器，并始终保留数据卷"""
        while True:
            try:
                await asyncio.sleep(self._config.cleanup_interval_seconds)
                await self._run_cleanup_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                started_at = time.time()
                error = f"cleanup loop failed: {exc}"
                logger.exception("Docker 沙箱清理循环异常")
                self._record_cleanup_result(started_at, [error])

    def _managed_user_ids_sync(self) -> set[int]:
        """列出 Docker 中已有的用户沙盒"""
        user_ids: set[int] = set()
        containers = self._get_client().containers.list(
            all=True,
            filters=self._container_filters(),
        )
        for container in containers:
            raw_user_id = container.labels.get(_USER_LABEL)
            try:
                user_ids.add(int(raw_user_id))
            except (TypeError, ValueError):
                logger.warning(
                    f"忽略包含无效用户标签的 Docker 沙箱: container={container.name}"
                )
        return user_ids

    def _cleanup_idle_container_sync(
        self,
        user_id: int,
        user_guard: _LifecycleGuard,
        start_lock: threading.Lock,
    ) -> None:
        """在没有活跃操作时停止或删除空闲用户容器"""
        with user_guard.try_maintenance() as acquired:
            if not acquired:
                return
            with start_lock:
                container = self._get_existing_container_sync(user_id)
                if container is None:
                    return
                self._persist_activity_sync(user_id, container)
                idle_seconds = self._idle_seconds(user_id)
                if idle_seconds < self._config.idle_stop_seconds:
                    return
                if idle_seconds >= self._config.idle_remove_seconds:
                    container.remove(force=True)
                    self._mark_user_not_running(user_id)
                    with self._activity_lock:
                        self._last_activity.pop(user_id, None)
                        self._last_persisted_activity.pop(user_id, None)
                    logger.info(
                        f"删除空闲 Docker 沙箱并保留持久化数据卷: user_id={user_id}"
                    )
                    return
                if container.status == "running":
                    container.stop(timeout=10)
                    self._mark_user_not_running(user_id)
                    logger.info(f"停止空闲 Docker 沙箱: user_id={user_id}")

    def _finalize_containers_sync(self) -> None:
        """持久化活动时间并按配置停止运行中容器"""
        containers = self._get_client().containers.list(
            all=True, filters=self._container_filters()
        )
        for container in containers:
            try:
                raw_user_id = container.labels.get(_USER_LABEL)
                try:
                    user_id = int(raw_user_id)
                    self._persist_activity_sync(user_id, container, force=True)
                except (TypeError, ValueError):
                    user_id = None
                except Exception:  # noqa: BLE001
                    user_id = None
                    logger.exception(
                        f"持久化 Docker 沙箱活跃时间失败: container={container.name}"
                    )
                container.reload()
                if (
                    self._config.stop_containers_on_shutdown
                    and container.status == "running"
                ):
                    container.stop(timeout=10)
                if user_id is not None:
                    self._mark_user_not_running(user_id)
            except NotFound:
                continue

    async def close(self) -> None:
        """停止后台任务并关闭 Docker 客户端"""
        self._capacity.close()
        cleanup_task = self._cleanup_task
        self._cleanup_task = None
        if cleanup_task is not None:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
        client = self._client
        if client is not None:
            try:
                await asyncio.to_thread(self._finalize_containers_sync)
            finally:
                self._client = None
                await asyncio.to_thread(client.close)


docker_sandbox_manager = DockerSandboxManager(cfg.sandbox)
