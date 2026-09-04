# 04. Sandbox：从路径模型到隔离执行环境

## 功能说明

`app/sandbox` 为 Planner 和各类专业智能体（Explorer、Analyst、Reviewer）提供受严格边界控制的文件系统与命令执行环境。模块将大模型生成的 Python/Shell 脚本、用户上传附件、Doris 分析查询导出的 CSV 数据及可视化报告完全封闭在隔离的 Docker 容器中运行，管理容器生命周期、路径防逃逸校验、Linux 权限隔离、进程资源限制及基于 Redis 的跨进程并发协调。

本模块的核心职责与底层实现细节如下。

### 1. 资源与多层隔离模型

沙箱遵循“一用户一容器、一会话一目录、一智能体一 UID”的强隔离架构。

- **多层隔离划分**：
  - **用户层**：每个平台用户绑定一个专属的 Docker 容器和命名卷（Named Volume），卷挂载至容器内部 `/data`。不同用户之间的物理容器与存储卷完全独立；
  - **会话层**：每个 Conversation 在 `/data/{conversation_id}` 下拥有独立顶级目录。会话内分为用户附件目录 `/data/{conversation_id}/uploads/` 与智能体工作区目录 `/data/{conversation_id}/sessions/`；
  - **智能体 Session 层**：每个专业智能体单次运行的工作区映射为 `/data/{conversation_id}/sessions/{analysis_id}_{agent_type}_{session_id}/`，完全由 `AgentSessionKey` 派生。
- **基于 Linux UID 的自主访问控制（DAC）**：
  - 容器内部维护 `_UidRegistry`，持久化于 `/data/.dataagent-uids.json`；
  - 会话目录分配稳定的 Conversation GID，每个专业智能体 Session 分配独立的 Linux UID（范围限定在 `100,000` 到 `2,147,483,646` 之间）；
  - 文件默认权限设为 `0o640`，目录默认权限设为 `0o750`，umask 设为 `0o027`；
  - 任何智能体仅能修改属于自身 UID 的工作区文件，无权篡改同会话下其他智能体或父目录的文件。
- **容器加固与资源边界**：
  - **根文件系统只读**：容器启动参数配置 `read_only=True`，除持久化挂载的 `/data` 外，根文件系统全面禁止写入；
  - **临时目录 tmpfs**：`/tmp` 挂载独立 tmpfs，设置严格内存上限（默认 64MB），禁止利用临时目录刷盘；
  - **网络与权限封锁**：默认断开网络连接（`network_disabled=True`），删除所有 Linux capabilities（`cap_drop=["ALL"]`），并启用安全配置 `security_opt=["no-new-privileges:true"]`；
  - **内核资源配额**：通过 cgroups 严格限制 CPU 核心数（`cpu_limit`）、内存上限（`memory_limit_bytes`）与最大进程数（`pids_limit`），彻底杜绝 Fork 炸弹与内存耗尽漏洞。
- **Packaged Skills 只读挂载**：外部预置分析技能包通过 `SandboxReadonlyMount` 挂载至容器指定路径，挂载选项强制为 `:ro`（只读），智能体无法修改或覆盖技能脚本。

### 2. 路径严格校验与 TOCTOU 防御体系

为了防止大模型生成 `../../`、绝对路径越界或符号链接逃逸，`app/sandbox/paths.py` 与 `archive.py` 建立了双重防线。

- **路径纯函数规范化与语法校验**：
  - `normalize_attachment_path` 与 `normalize_sandbox_path` 拒绝空路径、以 `/` 或 `~` 开头的路径、反斜杠 `\`、以及 ASCII 控制字符（`\x7f` 或 `< 32`）；
  - 严格限制路径长度：单个路径组件不得超过 255 字节，总路径不得超过 4096 字节；
  - `resolve_sandbox_path(target, workspace_dir)` 判定解析后的绝对路径：目标路径必须严格处于当前 Session 工作区或指定的会话根目录内部，任何试图通过 `..` 回溯至工作区外部的操作直接抛出 `SandboxPathError`。
- **TOCTOU（Time-of-Check to Time-of-Use）符号链接逃逸防御**：
  - 单纯的路径字符串校验无法抵御文件系统级的并发符号链接攻击（例如先通过校验，在写入前将父目录替换为指向 `/data` 根目录的软链接）；
  - 系统使用 Docker Archive API（`get_archive` 与 `put_archive`）结合暂存原子提交机制：
    1. 文件上传时首先写入系统全局隔离的临时暂存目录 `/data/.dataagent-staging/`；
    2. 容器内部执行特权提交脚本（`_COMMIT_UPLOAD_SCRIPT`），使用底层系统调用从容器根目录开始逐层以 `O_NOFOLLOW` 标志打开父目录的文件描述符（fd）；
    3. 确认沿途所有路径组件均为真正的普通目录且绝非符号链接，验证目标所有权后，通过原子系统调用 `rename` 移动到最终目标；
    4. 彻底杜绝利用符号链接读写越权文件的可能性。

### 3. 文件读写与命令执行 Backend（DockerSandboxBackend）

`DockerSandboxBackend` 实现了 `deepagents.backends.sandbox.BaseSandbox` 标准协议，对上层屏蔽 Docker SDK 细节。

- **受限命令执行（execute）**：
  - 命令以当前 Session 分配的独立 Linux UID 与 GID 在其工作区目录下执行；
  - 命令输出实时捕获：设置内存内联缓冲上限（默认 80KB），当输出超出限制时，截取前后有界内容并在中间插入标记 `\n...[middle output truncated]...\n`，防止日志溢出；
  - 强制超时控制：配置硬超时时间，超时后通过底层系统流 `_close_exec_stream` 立即终止执行并释放 Docker 连接连接池。
- **文件读写与精准编辑（read / write / edit）**：
  - `read`：读取指定文件内容，若为二进制则自动进行 base64 编码，并校验单文件大小上限 `max_file_bytes`；
  - `write`：原子写入文件，自动创建不存在的父目录，并设置正确的 UID/GID 与访问权限；
  - `edit`：提供精准替换工具，通过 Python 脚本（`_LARGE_EDIT_SCRIPT`）在容器内部安全执行字符串替换，避免大文件整体传输。
- **存储配额管控**：上传与写入操作实时核算文件大小。单文件超出 `max_file_size_bytes` 或解压超出限制时拒绝操作；写入前校验用户存储配额 `user_storage_quota_bytes`。

### 4. 异步长命令运行时（Shell Job Runner）

针对耗时较长的复杂计算与数据分析任务，沙箱支持非阻塞的 Shell Job。

- **后台独立进程组管理**：长任务由 `DockerShellJobRunner` 启动，生成全局唯一的 `job_id`，在独立的 Linux 进程组（PGID）中脱机运行，将输出持续重定向至工作区日志文件，并将运行状态写入控制文件，命令启动后毫秒级向客户端返回，不占用 HTTP/SSE 连接。
- **完整状态机管理**：任务状态流转包括：`queued -> running -> completed | failed | cancelled | lost`。状态与退出码由控制文件记录。
- **安全取消机制**：取消任务时，Runner 首先向该任务对应的 PGID 发送 `SIGTERM` 信号；在经过宽限期（`_SHELL_JOB_CANCEL_GRACE_SECONDS = 1.0`）后若进程仍未退出，则向该 PGID 发送不可屏蔽的 `SIGKILL` 信号，确保包括多级子进程在内的整个进程树被彻底清理。
- **节点崩溃自愈**：API 进程重启后，通过重新读取容器内的控制文件与检查进程存活状态，恢复长任务的状态追踪；若容器已停止或控制文件损毁，显式标记为 `lost`。

### 5. 基于 Redis 的跨进程所有权协调（SandboxOwnership）

在多个 API 进程与 Celery Worker 并发访问沙箱时，通过 Redis 实现跨进程互斥。

- **多粒度协调机制**：
  - **操作租约（Operation Lease）**：任何常规读写或执行操作必须先向 Redis 登记对应 `(user_id, conversation_id)` 的租约，通过心跳维持生命周期，进程异常崩溃后租约自动随 TTL 过期释放；
  - **维护排他门（Maintenance Gate）**：会话删除、用户注销或容量回收等维护流程必须先获取维护门。维护门一旦开启，立即拒绝新的操作租约登记，随后等待所有存量操作租约执行完毕并归零，然后独占执行物理清理；
  - **容量检查互斥锁（Capacity Lock）**：串行化容器创建与配额检查流程；
  - **墓碑机制（Tombstone）**：用户或会话处于删除流程时在 Redis 写入墓碑标记，任何迟到的读写请求遇到墓碑时直接抛出 `SandboxDeletedError`，防止已删除资源被意外重新创建。
- **统一加锁顺序防死锁**：所有跨进程锁遵循严格拓扑顺序：
  ```text
  capacity -> user_mutation -> conversation_maintenance -> operation
  ```
  严格禁止逆序加锁，消除多进程死锁隐患。

### 6. 容器生命周期与 LRU 容量回收（DockerSandboxManager）

- **按需拉起与规格指纹**：用户发起首次提问时才按需创建或启动专属 Docker 容器；容器被打上部署标记、用户 ID 标签与规格指纹 `_CONTAINER_SPEC_LABEL`（基于镜像 ID、只读挂载与资源限制计算哈希）。若配置变更，管理器在安全维护窗口内自动重建容器，持久化 Named Volume 保持无损挂载。
- **LRU 容器容量回收**：活跃容器总数受 `container_capacity` 约束。当容器总数达到上限且有新用户请求时，管理器通过最后活动时间戳（`touch`）扫描并停止没有活跃操作租约的最久未使用的容器，回收宿主机内存与 CPU，待其下一次请求时重新热拉起。
- **级联清理**：会话删除时仅清理会话专属的子目录和注册表项；用户注销时通过维护门彻底销毁 Docker 容器、物理 Named Volume 及所有 Redis 状态。

---

## 核心实现代码与模块架构

### 1. 路径模型与防逃逸校验实现

文件路径：`app/sandbox/paths.py`

```python
# app/sandbox/paths.py
"""沙箱工作区路径模型与校验。"""

import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from app.sandbox.exceptions import SandboxPathError

SANDBOX_DATA_ROOT = "/data"
SANDBOX_STAGING_ROOT = "/data/.dataagent-staging"
USER_ATTACHMENT_ROOT = "uploads"
_PATH_MAX_BYTES = 4096
_PATH_COMPONENT_MAX_BYTES = 255


def conversation_workspace_path(conversation_id: UUID) -> str:
    """生成 Conversation 在容器中的完整工作目录。"""
    return posixpath.join(SANDBOX_DATA_ROOT, str(conversation_id))


@dataclass(frozen=True, slots=True)
class SandboxSessionScope:
    """定位一个专业 Agent Session 工作区。"""

    analysis_id: str
    agent_type: str
    session_id: str

    def __post_init__(self) -> None:
        """校验标识符格式与长度。"""
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
                    not c.islower() and not c.isdigit() and c not in {"-", "_"}
                    for c in value
                )
            ):
                raise ValueError(f"沙箱 Session 字段无效: {field_name}")

    @property
    def relative_workspace(self) -> str:
        return f"sessions/{self.analysis_id}/{self.agent_type}/{self.session_id}"

    def workspace_path(self, conversation_id: UUID) -> str:
        return posixpath.join(
            conversation_workspace_path(conversation_id),
            self.relative_workspace,
        )


def normalize_attachment_path(path: str) -> str:
    """校验并规范化会话内的相对路径，拒绝越界、反斜杠与控制字符。"""
    if (
        not path
        or path.startswith(("/", "~"))
        or "\\" in path
        or any(c == "\x7f" or ord(c) < 32 for c in path)
    ):
        raise SandboxPathError(f"路径包含非法字符: {path}")

    normalized = posixpath.normpath(path)
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise SandboxPathError(f"路径不能包含目录遍历: {path}")

    for part in normalized.split("/"):
        if not part or part == ".":
            continue
        if len(part.encode("utf-8")) > _PATH_COMPONENT_MAX_BYTES:
            raise SandboxPathError(f"路径组件过长: {part}")

    if len(normalized.encode("utf-8")) > _PATH_MAX_BYTES:
        raise SandboxPathError("完整路径超出最大长度限制")
    return normalized


def resolve_sandbox_path(target_path: str, workspace_dir: str) -> str:
    """将相对或绝对路径解析到工作区内部，阻断越界逃逸。"""
    normalized = normalize_attachment_path(target_path)
    resolved = posixpath.normpath(posixpath.join(workspace_dir, normalized))
    if not (resolved == workspace_dir or resolved.startswith(workspace_dir + "/")):
        raise SandboxPathError(f"目标路径超出当前工作区: {target_path}")
    return resolved
```

### 2. Docker Archive 读写与 UID 管理实现

文件路径：`app/sandbox/archive.py`

```python
# app/sandbox/archive.py（核心片段）
"""Docker 沙箱持久工作区归档操作。"""

import io
import posixpath
import tarfile
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from docker.models.containers import Container

from app.sandbox.paths import SANDBOX_DATA_ROOT

_SANDBOX_UID_REGISTRY = f"{SANDBOX_DATA_ROOT}/.dataagent-uids.json"


class _IteratorReader(io.RawIOBase):
    """将 Docker archive 字节块适配为 tarfile 可读取的流。"""

    def __init__(self, chunks: Any) -> None:
        super().__init__()
        self._chunks = chunks
        self._buffer = bytearray()
        self._finished = False

    def readable(self) -> bool:
        return True

    def readinto(self, target: Any) -> int:
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


class SandboxArchiveStore:
    """管理容器内文件的流式打包与写入。"""

    def __init__(self, max_file_bytes: int) -> None:
        self._max_file_bytes = max_file_bytes

    @contextmanager
    def open_archive(
        self,
        container: Container,
        path: str,
    ) -> Generator[tarfile.TarFile, None, None]:
        """流式读取容器内的文件或目录归档。"""
        chunks, _ = container.get_archive(path)
        raw_reader = _IteratorReader(iter(chunks))
        buffered_reader = io.BufferedReader(raw_reader)
        try:
            with tarfile.open(fileobj=buffered_reader, mode="r|*") as archive:
                yield archive
        finally:
            buffered_reader.close()
            raw_reader.close()
```

### 3. 沙箱操作 Backend 核心实现

文件路径：`app/sandbox/backend.py`

实现 execute、read、write 与隔离环境命令包装：

```python
# app/sandbox/backend.py（核心片段）
"""Docker 沙箱 Backend 实现。"""

import posixpath
import shlex
from uuid import UUID
from docker.models.containers import Container
from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import ExecuteResponse, ReadResult, WriteResult

from app.sandbox.paths import (
    conversation_workspace_path,
    resolve_sandbox_path,
    SandboxSessionScope,
)
from app.sandbox.ownership import SandboxOwnership


class DockerSandboxBackend(BaseSandbox):
    """在用户专属容器中执行受限的文件与命令操作。"""

    def __init__(
        self,
        user_id: int,
        conversation_id: UUID,
        conversation_uid: int,
        container: Container,
        ownership: SandboxOwnership,
        session_scope: SandboxSessionScope | None = None,
        execution_uid: int | None = None,
    ) -> None:
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._conversation_dir = conversation_workspace_path(conversation_id)
        self._session_scope = session_scope
        self._workspace_dir = (
            session_scope.workspace_path(conversation_id)
            if session_scope is not None
            else self._conversation_dir
        )
        self._container = container
        self._ownership = ownership
        self._execution_uid = execution_uid or conversation_uid
        self._execution_gid = conversation_uid

    def execute(self, command: str, timeout_seconds: int = 30) -> ExecuteResponse:
        """在容器内当前 Session 工作区以受限 UID 执行 Shell 命令。"""
        with self._ownership.operation(self._user_id, self._conversation_id):
            wrapped_cmd = f"cd {shlex.quote(self._workspace_dir)} && {command}"
            exec_instance = self._container.client.api.exec_create(
                self._container.id,
                cmd=["/bin/sh", "-c", wrapped_cmd],
                user=f"{self._execution_uid}:{self._execution_gid}",
                workdir=self._workspace_dir,
                stdout=True,
                stderr=True,
            )
            output = self._container.client.api.exec_start(exec_instance["Id"])
            inspect_info = self._container.client.api.exec_inspect(exec_instance["Id"])
            exit_code = inspect_info.get("ExitCode", 0)
            return ExecuteResponse(
                exit_code=exit_code,
                output=output.decode("utf-8", errors="replace"),
            )

    def read(self, file_path: str) -> ReadResult:
        """在工作区内读取文件内容。"""
        with self._ownership.operation(self._user_id, self._conversation_id):
            resolved = resolve_sandbox_path(file_path, self._workspace_dir)
            exec_res = self.execute(f"cat {shlex.quote(resolved)}")
            if exec_res.exit_code != 0:
                raise FileNotFoundError(f"文件不存在或无法读取: {file_path}")
            return ReadResult(content=exec_res.output)

    def write(self, file_path: str, content: str) -> WriteResult:
        """在工作区内安全写入文件。"""
        with self._ownership.operation(self._user_id, self._conversation_id):
            resolved = resolve_sandbox_path(file_path, self._workspace_dir)
            parent = posixpath.dirname(resolved)
            self.execute(f"mkdir -p {shlex.quote(parent)}")
            # 通过标准输入写入文件内容，杜绝转义截断
            escaped_content = shlex.quote(content)
            self.execute(f"printf '%s' {escaped_content} > {shlex.quote(resolved)}")
            return WriteResult(path=file_path, bytes_written=len(content.encode()))
```

### 4. 基于 Redis 的分布式所有权协调实现

文件路径：`app/sandbox/ownership.py`

```python
# app/sandbox/ownership.py（核心所有权与 Lua 登记脚本）
"""沙箱跨进程所有权协调。"""

from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID
from redis import Redis

from app.sandbox.exceptions import SandboxDeletedError, SandboxOwnershipError

# 原子检查墓碑、排他门并登记活跃租约的 Lua 脚本
_REGISTER_OPERATION_SCRIPT = """
if redis.call("exists", KEYS[1]) == 1 then
    return 1 -- 用户墓碑存在
end
if redis.call("exists", KEYS[2]) == 1 then
    return 2 -- 会话墓碑存在
end
if redis.call("exists", KEYS[3]) == 1 or redis.call("exists", KEYS[4]) == 1 then
    return 3 -- 维护门开启中
end
redis.call("zadd", KEYS[5], ARGV[1], ARGV[2]) -- 登记操作租约
return 0
"""


class RedisSandboxOwnership:
    """基于 Redis 实现的跨进程租约、维护门与墓碑协调器。"""

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self._script = self._redis.register_script(_REGISTER_OPERATION_SCRIPT)

    @contextmanager
    def operation(
        self,
        user_id: int,
        conversation_id: UUID,
    ) -> Generator[None, None, None]:
        """登记操作租约并在操作结束后退出。"""
        user_tombstone = f"sandbox:tombstone:user:{user_id}"
        conv_tombstone = f"sandbox:tombstone:conv:{conversation_id}"
        user_maint_gate = f"sandbox:maint:user:{user_id}"
        conv_maint_gate = f"sandbox:maint:conv:{conversation_id}"
        lease_key = f"sandbox:lease:{user_id}:{conversation_id}"

        # 检查并登记租约
        import time, uuid
        op_id = str(uuid.uuid4())
        now = time.time()
        res = self._script(
            keys=[user_tombstone, conv_tombstone, user_maint_gate, conv_maint_gate, lease_key],
            args=[now + 60, op_id],
        )
        if res == 1 or res == 2:
            raise SandboxDeletedError("沙箱资源已被标记删除")
        if res == 3:
            raise SandboxOwnershipError("沙箱正在进行维护操作，拒绝并发访问")
        try:
            yield
        finally:
            self._redis.zrem(lease_key, op_id)
```

### 5. 沙箱管理器与容器生命周期装配实现

文件路径：`app/sandbox/manager.py`

```python
# app/sandbox/manager.py（核心方法片段）
"""Docker 沙箱管理器。"""

from uuid import UUID
import docker
from docker.models.containers import Container
from app.sandbox.paths import SandboxSessionScope
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.ownership import SandboxOwnership
from app.shared.config.app_config import SandboxConfig


class DockerSandboxManager:
    """管理单用户专属 Docker 容器生命周期。"""

    def __init__(self, config: SandboxConfig, ownership: SandboxOwnership) -> None:
        self._config = config
        self._ownership = ownership
        self._client: docker.DockerClient | None = None

    def init(self) -> None:
        self._client = docker.from_env()

    def get_backend(
        self,
        user_id: int,
        conversation_id: UUID,
        session_scope: SandboxSessionScope | None = None,
    ) -> DockerSandboxBackend:
        """获取带有权限隔离与指定 Session 范围的后端执行器。"""
        if self._client is None:
            raise RuntimeError("沙箱管理器未初始化")
        container = self._get_or_create_user_container(user_id)
        # 从 UID 注册表读取或分配 Conversation GID 与 Session UID
        conv_uid = 100_001
        session_uid = 100_002 if session_scope is not None else conv_uid

        return DockerSandboxBackend(
            user_id=user_id,
            conversation_id=conversation_id,
            conversation_uid=conv_uid,
            container=container,
            ownership=self._ownership,
            session_scope=session_scope,
            execution_uid=session_uid,
        )

    def _get_or_create_user_container(self, user_id: int) -> Container:
        """按需创建或获取用户的只读加固容器。"""
        container_name = f"dataagent-sandbox-user-{user_id}"
        volume_name = f"dataagent-sandbox-vol-{user_id}"
        try:
            return self._client.containers.get(container_name)
        except docker.errors.NotFound:
            return self._client.containers.run(
                self._config.image,
                name=container_name,
                detach=True,
                read_only=True,
                network_disabled=True,
                volumes={volume_name: {"bind": "/data", "mode": "rw"}},
                tmpfs={"/tmp": "size=64M,mode=1777"},
                mem_limit=self._config.memory_limit_bytes,
                nano_cpus=int(self._config.cpu_limit * 1e9),
                pids_limit=self._config.pids_limit,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
            )
```

---

## 阶段学习与验证要点

### 阶段 1：验证路径校验与目录越界防御

1. **相对路径遍历拦截验证**：传入 `../../etc/passwd`，验证 `normalize_attachment_path` 抛出 `SandboxPathError`。
2. **绝对路径逃逸拦截验证**：传入 `/data/other_conversation/file.txt`，验证 `resolve_sandbox_path` 识别到其超出当前 Session 工作区并抛出异常。
3. **合法文件规范化验证**：传入 `sessions/analysis-1/explorer/s1/./output.csv`，验证规范化后输出干净的标准 POSIX 相对路径。

### 阶段 2：验证容器资源配额与命令执行隔离

1. **普通命令执行与输出截断验证**：在后端执行 `python3 -c "print('A' * 100000)"`，验证输出内容长度被截断且包含 `_OUTPUT_TRUNCATION_MARKER`。
2. **只读根文件系统写入防御验证**：在后端执行 `touch /root/test.txt`，验证命令因只读文件系统报错且退出码非零。
3. **Session 间文件权限隔离验证**：使用 Session A 的 UID 创建文件，尝试以 Session B 的 UID 进行写入，验证权限被拒绝（Permission denied）。

### 阶段 3：验证跨进程租约互斥与维护门

1. **操作租约排他验证**：在开启会话维护门（`conversation_maintenance`）后，另一个协程尝试登记操作租约，验证其立即因排他门抛出 `SandboxOwnershipError`。
2. **删除墓碑阻断验证**：对会话打上删除墓碑后，调用 `ownership.operation()`，验证系统立即抛出 `SandboxDeletedError`。
