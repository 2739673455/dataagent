# Sandbox 模块功能

`sandbox` 为 Agent 提供受限 Docker 执行环境和持久化文件工作区。用户拥有独立的 Named Volume 与 Container；Conversation 和 Specialist Session 在同一用户卷内使用不同 Linux UID/GID 隔离。

## 工作区与文件

```text
/data/{conversation_id}
├── uploads/
└── sessions/{analysis_id}/{agent_type}/{session_id}/

/data/.dataagent-staging/
└── {conversation_id}/{execution_uid}/
```

首次写入会创建用户 Volume、停止状态的 Container、Conversation 工作区和 UID 注册表。Session 目录使用独立执行 UID，对话 GID 用于同一 Conversation 内的受控读取。

路径规则在文件工具、`shell`、`view_image` 和产物链接中一致：相对路径以当前 workspace 为基准，绝对路径保留容器路径。专业 Agent 只可修改自己的 Session；用户附件只可修改 `uploads/`。

上传、系统产物写入和文件提交通过 staging、`dir_fd`、`O_NOFOLLOW` 与原子 replace 完成。`max_file_bytes` 限制上传、文件工具写入和 Shell Job 日志。用户卷总容量由 `max_user_storage_bytes` 交给支持硬配额的 Volume Driver；本地 `local` Volume 不模拟该配额。

下载、下载资格检查和空删除只读取已有 Container、UID 注册表和目录。它们不会创建 Volume、Container、Conversation 或 Session。

## 命令与 Shell Job

普通 Backend 命令与模型 `shell` 都使用当前 Session UID/GID、工作目录、HOME 和临时目录。直接命令受 `internal_command_timeout_seconds` 限制；模型 `shell` 使用独立进程组，可在 60 秒前台等待后转为后台任务。前台结束时不公开 Shell Job；发生内联截断时保留日志文件并在输出末尾附加其路径。

两种命令的内联输出最多保留 80 KB：输出超限时保留开头和结尾，在中间插入截断标记。Shell Job 的完整合并 stdout/stderr 写入 `large_tool_results/shell_jobs/{job_id}.log`，日志达到 `max_file_bytes` 后继续排空并标记截断。取消先发送 TERM，再发送 KILL 给整个进程组。

长 Shell Job 在整个执行期间持有 Redis operation lease，空闲回收不会停止仍在运行的任务。

## 并发与生命周期

Redis ownership 是跨进程协调的唯一事实来源：

- `operation(user_id, conversation_id)` 防止操作中的资源被删除。
- `conversation_maintenance` 串行化目标 Conversation 的 Archive 提交、Session 删除与 Conversation 删除。
- `user_maintenance` 用于用户删除、容器回收和运行时关闭。
- `user_mutation` 串行化用户 Container、Volume 与 UID 注册表的结构变更。
- `capacity` 串行化运行容器的 Docker 状态检查、启动和停止。

每个公开用例完成后向 Redis 写入一次活动时间。启动扫描发现运行 Container 没有活动记录时以当前时间初始化，避免其被立即回收。Redis 故障会使 Sandbox 操作失败。

容量决策在 Redis capacity lock 中直接读取 Docker：目标已运行时直接返回；有空位时启动；满载时停止最久未活动且没有 operation lease 的 Container；仍无空位时返回容量不可用。Sandbox 不维护进程内 FIFO、预留状态或等待队列。

空闲超过 `idle_stop_seconds` 时停止 Container，超过 `idle_remove_seconds` 时删除 Container，用户 Volume 保留。用户删除会删除 Container 和 Volume；Conversation 删除移除对应目录和 UID 注册项。Redis tombstone 让删除后的操作失败，并允许删除任务幂等重试。

## Docker 安全边界

Container 使用只读根文件系统，`/data` 为读写 Volume，`/tmp` 为 `nosuid,nodev` tmpfs。运行时移除所有 capabilities，启用 `no-new-privileges`，并限制网络、CPU、内存和 PID。Agent Skill 通过只读 bind mount 暴露到 `/skills/{agent}`；路径校验拒绝对 Skill 目录的写入。

## 代码位置

- `app/sandbox/manager.py`：资源准备、附件 API、删除与清理调度。
- `app/sandbox/runtime_pool.py`：Container 运行容量、启动、空闲回收与关闭。
- `app/sandbox/backend.py`：DeepAgents 文件接口和执行 UID/GID。
- `app/sandbox/shell_runner.py`：Shell Job 的 Docker 包装进程、取消和控制文件协议。
- `app/sandbox/archive.py`：UID 注册表、停止或运行容器中的 Archive 文件操作。
- `app/sandbox/ownership.py`：Redis operation、maintenance、tombstone、容量锁和活动记录。
- `app/sandbox/paths.py`：路径与 Session scope 校验。
