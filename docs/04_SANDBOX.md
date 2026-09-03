# 04. Sandbox 模块职责与实现

`sandbox` 为 Planner 和专业 Agent 提供受限 Docker 执行环境、持久化文件工作区，以及跨 FastAPI、Celery Worker 和清理任务一致的资源生命周期。

## 模块职责与边界

`sandbox` 负责把用户文件、Conversation、Analysis 和专业 Agent Session 映射到隔离的 Docker 资源与目录权限。它同时提供文件读写、附件上传下载、命令执行、Shell Job、运行容量控制、空闲回收和资源删除能力。

主要使用者包括：

- `assistant` 为 Planner 和 Specialist 创建 Backend，并将文件、Shell 和图片工具提供给 Agent。
- `query` 将完整 SQL 结果写入当前 Explorer Session。
- 附件接口将用户上传文件保存到 Conversation 的 `uploads` 目录。
- `assistant` 和 `workflows` 在删除 Conversation 或用户时调用沙箱清理能力。

`sandbox` 不判断业务用户能否分析数据，也不管理 Conversation 数据库记录。调用方必须先完成身份和资源归属校验，再使用沙箱能力。

## 功能清单

```text
Sandbox
→ 管理用户级 Docker 资源
→ 建立 Conversation 与 Agent Session 工作区
→ 限制文件访问范围
→ 读写附件和 Agent 产物
→ 执行普通命令和 Shell Job
→ 协调跨进程资源所有权
→ 控制运行容量并回收空闲 Container
→ 删除 Session、Conversation 和用户资源
→ 提供 Docker 安全边界
```

## 1. 管理用户级 Docker 资源

**实现目的**

让不同用户的代码执行和持久化文件拥有独立资源边界，同时允许 Container 停止或重建后继续使用原文件。

**使用者与使用方式**

- `assistant` 首次为用户创建 Agent Backend 时按需准备资源。
- 附件上传和查询结果写入也会按需准备用户存储。
- 运维人员通过 `sandbox.image`、CPU、内存、PID 和 Volume 配置控制资源规格。

**具体实现**

```text
首次发生写入或执行
→ 按 deployment_namespace + user_id 计算资源名
→ 创建用户专属 Docker Named Volume
→ 创建挂载该 Volume 的用户专属 Container
→ 使用 Label 标记部署与用户归属
→ 保存 Container 规格摘要

后续使用
→ 复用规格一致的现有资源
→ Container 可以停止、重新启动或重建
→ Named Volume 持续保存用户文件
```

只读检查不会创建缺失的 Volume 或 Container，避免下载检查、空删除等读取操作意外产生资源。

### 设计细节：稳定名称和归属标签共同确定资源所有权

Container 与 Volume 名称包含部署命名空间和用户 ID，标签再次记录归属与配额：

```python
def _container_name(self, user_id: int) -> str:
    return f"dataagent-{self._config.deployment_namespace}-sandbox-user-{user_id}"

def _volume_name(self, user_id: int) -> str:
    return f"{self._container_name(user_id)}-data"

def _resource_labels(self, user_id: int) -> dict[str, str]:
    return {
        _DEPLOYMENT_LABEL: self._config.deployment_namespace,
        _USER_LABEL: str(user_id),
        _QUOTA_BYTES_LABEL: str(self._config.max_user_storage_bytes),
    }
```

获取已有 Volume 时必须验证标签、驱动和驱动参数，名称相同但归属不符会直接报错。新建 Container 挂载用户 Volume，并写入完整运行规格摘要：

```python
container = client.containers.create(
    self._config.image,
    name=self._container_name(user_id),
    volumes={
        volume.name: {"bind": SANDBOX_DATA_ROOT, "mode": "rw"},
        **self._readonly_mount_volumes(),
    },
    labels={
        **self._resource_labels(user_id),
        _CONTAINER_SPEC_LABEL: self._container_spec,
    },
    **self._runtime_container_spec(),
)
```

Container 创建后保持停止状态，只有执行命令时才进入运行容量管理。文件类操作可以复用停止状态的 Container 与 Volume，不会无意义占用运行名额。

## 2. 建立 Conversation 与 Agent Session 工作区

**实现目的**

让同一用户的多个 Conversation、Analysis 和 Specialist Session 能够共享必要产物，同时限制每个 Agent 的写入范围。

**使用者与使用方式**

- 用户附件写入 Conversation 的 `uploads`。
- Explorer、Analyst 和 Reviewer 使用各自的 Session 目录。
- Planner 读取 Conversation 内的文件和专业 Agent 产物。
- 下游专业 Agent 读取同一 Conversation 中上游 Session 的产物。

**具体实现**

```text
/data/{conversation_id}/
├── uploads/
└── sessions/{analysis_id}/{agent_type}/{session_id}/

/data/.dataagent-staging/
└── {conversation_id}/{execution_uid}/
```

- 每个专业 Session 从 UID 注册表获得独立 Linux UID。
- 同一 Conversation 使用受控 GID 提供跨 Session 只读访问。
- `SandboxSessionScope` 统一校验 `analysis_id`、`agent_type` 和 `session_id`，并生成工作目录与 Checkpoint namespace 使用的稳定标识。
- UID 注册表保存在用户 Volume 中，因此多个应用进程看到一致的 Session 身份。


### 设计细节：一个用户共享 Volume，每个 Conversation 和 Session 使用稳定 Linux UID

用户级 Named Volume 用于持久化文件；Conversation 和 Specialist Session 通过不同 UID/GID 隔离写权限。UID 注册表由 root 拥有并以 `0600` 保存，模型进程不能修改。分配算法以稳定业务标识作为种子，碰撞时继续探测未使用 UID：

```python
@staticmethod
def _allocate_uid(seed: bytes, used_uids: set[int]) -> int:
    uid_range = _MAX_SANDBOX_UID - _MIN_SANDBOX_UID + 1
    for attempt in range(uid_range):
        digest = hashlib.blake2s(seed + attempt.to_bytes(8, "big")).digest()
        candidate = (
            _MIN_SANDBOX_UID
            + int.from_bytes(digest[:8], "big") % uid_range
        )
        if candidate not in used_uids:
            return candidate
    raise RuntimeError("沙箱 UID 分配范围已耗尽")
```

注册表加载时会验证格式、版本、UID 范围和全局唯一性。已有目录的实际 owner 必须与注册表一致，否则停止准备工作区，避免在未知归属的目录上继续执行。

## 3. 限制文件访问范围

**实现目的**

阻止路径穿越、符号链接绕过、跨用户访问和专业 Agent 修改其他 Session 产物。

**使用者与使用方式**

- Planner 和专业 Agent 通过统一文件 Backend 使用相同路径规则。
- `shell`、`view_image`、附件下载和产物链接也调用同一套路径解析与归属检查。
- Agent 可以使用相对工作区路径或 `/data/...` 容器绝对路径。

**具体实现**

- 相对路径以当前 Agent workspace 为基准解析。
- 绝对路径必须位于合法容器数据目录。
- 专业 Agent 只能修改自己的 Session。
- 其他 Session 和用户上传文件根据 Linux UID/GID 与服务端规则只读。
- Planner 的文件与 Shell 能力施加只读使用约束。
- Skill 目录位于 `/skills/{agent}`，通过只读挂载提供，路径校验拒绝写入。
- 写入使用 staging、目录文件描述符、`O_NOFOLLOW` 和原子替换，避免目标路径在检查后被替换。


### 设计细节：路径校验分为语法规范化和工作区授权

路径语法层拒绝反斜杠、控制字符、`~`、超长路径和超长组件，并使用 POSIX 语义处理相对路径。附件路径还明确拒绝绝对路径、`.` 与 `..` 组件：

```python
def normalize_attachment_path(path: str) -> str:
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
        or len(part.encode("utf-8", errors="surrogatepass"))
        > _PATH_COMPONENT_MAX_BYTES
        for part in parts
    ):
        raise SandboxPathError(path)
    return PurePosixPath(*parts).as_posix()
```

Backend 再执行授权层检查。读取路径按 Shell 当前目录解析，写入和编辑必须落在当前 Specialist Session 的工作区内：

```python
def _resolve_mutation_path(self, path: str) -> str:
    resolved_path = self._resolve_path(path)
    if not (
        resolved_path == self._workspace_dir
        or resolved_path.startswith(f"{self._workspace_dir}/")
    ):
        raise SandboxPathError(path)
    return resolved_path
```

因此 `../` 经过规范化后即使形成合法绝对路径，只要离开当前工作区也无法用于变更文件。对外下载还会把绝对路径转换回 Conversation 相对路径，并只接受 `sessions` 或 `uploads` 两个公开根。

## 4. 读写附件和 Agent 产物

**实现目的**

用同一套安全文件协议处理用户附件、查询 CSV、脚本、图表和报告，并确保只有属于当前 Conversation 的真实文件能够下载。

**使用者与使用方式**

- 用户通过附件 API 上传、获取和删除 `uploads` 中的文件。
- Agent 使用 `read_file`、`write_file` 和 `edit_file` 操作当前工作区。
- `query` 使用 `write_artifact` 原子提交完整 CSV。
- `assistant` 在消息投影时调用下载资格检查，将合法文件转换为附件或产物链接。

**具体实现**

```text
写入文件
→ 规范化 Conversation 和目标路径
→ 检查用户与 Conversation 未被删除
→ 在 staging 目录生成完整内容
→ 校验单文件大小
→ 使用目标目录 fd 和 O_NOFOLLOW 检查路径
→ 原子提交到最终位置

下载文件
→ 只读取现有 Container、UID 注册表和目录
→ 检查文件真实存在、类型正确且 UID 可访问
→ 流式返回文件内容
```

`max_file_bytes` 同时限制附件、文件工具写入、查询产物和 Shell 日志。`max_user_storage_bytes` 交给支持硬配额的 Volume Driver；本地 `local` Volume 不模拟硬配额。


### 设计细节：文件上传先进入 root 暂存区，再受控原子提交

Docker `put_archive` 由守护进程权限写入，直接写目标路径会绕开 Session 用户权限。Backend 先使用不可预测文件名写入 root 专属暂存目录，再运行受控脚本验证工作区、目录 owner、目标相对路径和文件模式，最后原子替换目标：

```python
staging_name = f"upload-{secrets.token_hex(20)}"
staging_path = posixpath.join(self._staging_dir, staging_name)
try:
    if not self._container.put_archive(self._staging_dir, archive_buffer):
        raise OSError(f"暂存上传文件失败: {path}")

    commit_result = self._container.exec_run(
        ["python3", "-c", _COMMIT_UPLOAD_SCRIPT, payload],
        user="0",
        privileged=True,
        workdir=SANDBOX_DATA_ROOT,
    )
    if commit_result.exit_code != 0:
        raise OSError("提交上传文件失败")
finally:
    self._container.exec_run(
        ["rm", "-f", "--", staging_path],
        user="0",
        privileged=True,
        workdir=SANDBOX_DATA_ROOT,
    )
```

失败路径同样删除暂存文件，避免 root 文件绕过工作区配额长期累积。普通附件 API 使用 Archive 能力时无需启动 Container；只有 Agent 执行和 Session 删除等需要容器内命令的操作才申请运行容量。

## 5. 执行普通命令和 Shell Job

**实现目的**

让 Agent 在隔离环境中运行数据处理和报告生成命令，并支持超过一次工具调用等待时间的长任务。

**使用者与使用方式**

- Backend 内部命令用于文件与环境操作。
- 专业 Agent 使用 `shell` 启动命令。
- Agent 使用 `list_shell_jobs`、`get_shell_job` 和 `cancel_shell_job` 管理后台任务。
- Planner 的 Shell 受到只读约束。

**具体实现**

```text
启动命令
→ 使用当前 Session UID、GID、工作目录、HOME 和临时目录
→ 在独立进程组中运行
→ 前台等待窗口内完成时直接返回
→ 超时后保留进程并返回 job_id

后台运行
→ 完整 stdout/stderr 合并写入受限日志文件
→ 内联结果最多返回配置允许的开头与结尾
→ 查询接口返回状态、运行时间和有限输出
→ 取消时先向进程组发送 TERM
→ 宽限期后仍运行则发送 KILL
```

Shell Job 运行期间持有 Redis operation lease，空闲回收和生命周期删除会等待该操作结束。委派结束时 `assistant` 会清理遗留任务。


### 设计细节：Shell Job 把日志文件和控制状态分开保存

长任务由包装进程创建独立进程组。业务输出持续写入 Session 可见的日志文件，PID、最终状态和截断标记写入模型不可见的 staging 控制文件。工具返回只内联最多 80 KB，并在超限时保留头尾；完整日志仍可作为文件读取。

```python
relative_log_path = f"large_tool_results/shell_jobs/{job_id}.log"
return (
    posixpath.join(self._backend.workspace_dir, relative_log_path),
    posixpath.join(
        self._backend._staging_dir,
        "shell_jobs",
        f"{job_id}.json",
    ),
)
```

取消时受控脚本先向整个进程组发送 TERM，等待宽限时间后再发送 KILL。这样子进程不会在工具调用结束后继续占用资源。包装进程或 Worker 中断时，如果无法读取合法终态，结果标记为 `interrupted`，不会把不完整输出误报为成功。

## 6. 协调跨进程资源所有权

**实现目的**

让 FastAPI、多个 Celery Worker、Beat 和清理协程对同一 Docker 资源做出一致决策，防止执行、删除、回收和目录变更互相竞争。

**使用者与使用方式**

- 所有公开沙箱操作自动获取对应租约或锁。
- Conversation 删除、用户注销和空闲回收通过 maintenance 锁等待正在执行的操作。
- 运维人员需要为所有进程配置相同的 `sandbox.ownership.redis_url` 和部署命名空间。

**具体实现**

- `operation(user_id, conversation_id)`：标记正在读取、写入或执行的 Conversation。
- `conversation_maintenance`：串行化 Session 删除、Archive 提交和 Conversation 删除。
- `user_maintenance`：串行化用户删除、运行时关闭和用户级清理。
- `user_mutation`：串行化 Container、Volume 和 UID 注册表的结构变化。
- `capacity`：串行化全局运行 Container 容量决策。
- 活动时间记录最近一次公开操作，用于选择空闲资源。
- Conversation 和用户删除标记使后续操作立即失败，并支持清理任务幂等重试。

Redis 是跨进程 ownership 状态的事实来源。Redis 故障时相关操作失败，系统不会退化为仅进程内锁。


### 设计细节：公开操作和删除维护通过 Redis lease 建立互斥

每次文件或命令操作先在用户级和 Conversation 级有序集合中登记同一个随机 token，score 是租约过期时间。登记由 Lua 脚本原子完成，同时检查用户墓碑、Conversation 墓碑和维护 gate：

```python
status = int(
    self._redis.eval(
        _REGISTER_OPERATION_SCRIPT,
        len(keys),
        *keys,
        str(time.time() + self._lease_seconds),
        token,
    )
)
if status == 1:
    raise SandboxDeletedError("用户沙箱已被删除")
if status == 2:
    raise SandboxDeletedError("会话沙箱已被删除")
```

运行时后台线程持续续租；操作结束后从两个集合移除 token。维护操作先独占 gate，再等待已有 lease 全部结束：

```python
@contextmanager
def conversation_maintenance(self, user_id: int, conversation_id: UUID):
    label = f"conversation:{user_id}:{conversation_id}"
    with self._lock(f"{label}:gate"):
        self._wait_for_idle(
            self._active_conversation_key(user_id, conversation_id),
            label,
        )
        yield
```

这个协议覆盖 API 与 Celery 的跨进程并发。进程崩溃后 lease 自动过期，维护任务不会永久等待；续租失败会让当前操作以明确错误结束。

## 7. 控制运行容量并回收空闲 Container

**实现目的**

限制同时运行的用户 Container 数量，优先保留正在执行或近期活跃的资源，并在不删除用户文件的前提下释放计算资源。

**使用者与使用方式**

- Agent 执行命令时由 Runtime Pool 自动确保目标 Container 运行。
- 后台清理循环按配置停止或删除空闲 Container。
- 运维人员配置最大运行数量、停止阈值和删除阈值。

**具体实现**

```text
请求启动用户 Container
→ 获取 Redis capacity lock
→ 读取 Docker 实时运行状态
→ 目标已运行时直接使用
→ 有空位时启动目标
→ 满载时选择最久未活动且没有 operation lease 的 Container
→ 停止被选中的 Container 后启动目标
→ 没有可回收资源时返回容量不可用
```

空闲超过 `idle_stop_seconds` 时停止 Container；超过 `idle_remove_seconds` 时删除 Container。两种操作都保留 Named Volume。启动扫描会为缺少活动记录的运行 Container 写入当前时间，防止服务重启后立即回收。


### 设计细节：容量回收遵守固定锁顺序并二次确认

启动 Container 时先在容量锁下读取真实运行数量。容量已满会按 Redis 活动时间挑选最久未使用的用户，跳过仍有 operation lease 的候选。容量锁不能在等待用户操作结束时一直持有，因此实现先释放容量锁，再按“用户维护锁 → 容量锁”的顺序重新取得资源并二次检查：

```python
for idle_user_id in candidates:
    if self._ownership.is_user_active(idle_user_id):
        continue
    with (
        self._ownership.user_maintenance(idle_user_id),
        self._ownership.capacity(),
    ):
        current = self._get_existing_container(idle_user_id)
        if (
            current is None
            or current.status != "running"
            or self._ownership.is_user_active(idle_user_id)
        ):
            continue
        if len(self._running_containers()) < self._config.max_running_containers:
            break
        current.stop(timeout=10)
        break
```

二次检查用于处理锁释放后状态已经变化的情况。没有安全候选时返回容量不足，不会终止一个仍在运行分析任务的 Container。

## 8. 删除 Session、Conversation 和用户资源

**实现目的**

按资源归属提供粒度明确、可以安全重试的清理能力，使 Agent 修补、对话删除和用户注销能够只删除自己的目标范围。

**使用者与使用方式**

- Planner 删除不再需要的专业 Agent Session。
- `assistant` 删除 Conversation 时清理对应目录和 UID 注册。
- `workflows` 完成用户注销时删除全部 Container、Volume 和 Redis ownership 状态。

**具体实现**

- Session 删除只移除对应 Session 目录及 UID 注册项。
- Conversation 删除先写删除标记、等待 operation lease 结束，再删除 Conversation 目录和相关 UID 注册项。
- 用户删除先阻止新操作并等待现有操作结束，再删除 Container、Named Volume 与 ownership 状态。
- 清理接口允许目标已经不存在，失败后可以从已完成步骤继续重试。
- 普通空闲回收不会删除用户 Volume；用户注销会删除 Volume 中的全部持久化文件。

### 设计细节：不同删除粒度使用各自的维护锁和墓碑

Session 删除取得 Conversation 维护锁，只清理稳定 `SandboxSessionScope` 对应的目录和 UID；若 Conversation 或用户已进入删除状态，`assert_available()` 会阻止新的局部操作：

```python
scope = SandboxSessionScope(analysis_id, agent_type, session_id)
with self._ownership.conversation_maintenance(user_id, conversation_id):
    self._ownership.assert_available(user_id, conversation_id)
    container = self._runtime_pool.get_running(user_id)
    deleted = self._archive.delete_session(
        container,
        conversation_id,
        scope,
    )
```

Conversation 与用户删除先写 ownership 墓碑，再清理物理资源。用户级清理同时取得维护锁、容量锁和变更锁，并允许 Docker 资源已经不存在：

```python
with (
    self._ownership.user_maintenance(user_id),
    self._ownership.capacity(),
    self._ownership.user_mutation(user_id),
):
    self._ownership.mark_user_deleted(user_id)
    with suppress(NotFound):
        client.containers.get(self._container_name(user_id)).remove(force=True)
    with suppress(NotFound):
        client.volumes.get(self._volume_name(user_id)).remove(force=True)

await asyncio.to_thread(self._ownership.forget_user, user_id)
```

墓碑在清理窗口内阻止资源被并发重建。Docker 删除完成后才移除 Redis ownership 状态；任务失败时墓碑和剩余资源仍可由同一清理入口继续处理。

## 9. 提供 Docker 安全边界

**实现目的**

限制 Agent 生成代码和第三方分析工具对宿主机、网络与其他用户资源的影响。

**使用者与使用方式**

- Agent 自动在受限 Container 中执行，无需自行配置 Docker 参数。
- 运维人员构建并配置固定沙箱镜像，运行中的 Container 不安装新依赖。

**具体实现**

- 根文件系统只读，`/data` 作为用户读写 Volume。
- `/tmp` 使用带 `nosuid,nodev` 的受限 tmpfs。
- Container 默认断网运行。
- 移除 Linux capabilities，启用 `no-new-privileges`。
- 限制 CPU、内存和 PID 数量。
- Skill 以只读 bind mount 挂载。
- Container 规格摘要用于识别镜像或安全配置变化，并在安全时重建旧规格资源。


### 设计细节：容器规格参与稳定摘要，删除先写墓碑

容器使用只读根文件系统、受限 tmpfs、全部 capability drop、`no-new-privileges`、网络模式和资源上限。镜像不可变 ID、运行参数、只读挂载和 Volume 策略一起计算规格摘要：

```python
def _runtime_container_spec(self) -> dict[str, Any]:
    return {
        "command": ["sleep", "infinity"],
        "init": True,
        "read_only": True,
        "user": "1000:1000",
        "working_dir": SANDBOX_DATA_ROOT,
        "tmpfs": {"/tmp": "rw,nosuid,nodev,size=256m"},
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": self._config.memory_limit,
        "nano_cpus": self._config.nano_cpus,
        "pids_limit": self._config.pids_limit,
        "network_mode": self._config.network_mode,
        "environment": {"HOME": "/tmp"},
    }
```

已有 Container 的摘要与当前规格不同，会在安全维护窗口重建，Named Volume 保留。Conversation 或用户删除会在维护锁内先写 Redis 墓碑，再删除目录、Container 或 Volume；之后到达的迟到请求会在 `assert_available()` 阶段被拒绝，无法重新创建已删除资源。

## 数据与配置

```text
持久化数据
→ Docker Named Volume：附件、Session 文件、查询 CSV、脚本、图表和报告
→ Volume 内 UID 注册表：专业 Session 与 Linux UID/GID 映射
→ Redis：锁、租约、活动时间和删除标记

主要配置
→ sandbox.image
→ sandbox.max_running_containers
→ sandbox.max_file_bytes
→ sandbox.max_user_storage_bytes
→ sandbox.idle_stop_seconds
→ sandbox.idle_remove_seconds
→ sandbox.ownership
```
