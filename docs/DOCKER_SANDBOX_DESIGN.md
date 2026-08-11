# 本地 Docker 多用户沙盒设计

## 1. 文档状态

本文记录 DataAgent 本地 Docker 沙盒的目标方案和已经确定的技术决策。

当前阶段不引入 MinIO，不使用 gVisor，也不处理多个 FastAPI 进程或多节点之间的沙盒状态协调。本文范围内的单进程、单 Docker 主机方案已经实现，实际状态见“落地状态”章节。

## 2. 设计结论

- 每个用户拥有一个 Docker 容器和一个持久化 Docker Named Volume。
- 同一用户的所有会话共享该用户的 Volume，但分别存放在独立目录中。
- 每个会话使用独立 Linux UID/GID，并通过 `0700` 目录权限实现会话文件隔离。该机制不提供会话级 CPU、内存、PID、网络或内核隔离；这些资源仍由用户容器共享。
- 用户容器只在需要执行沙盒命令时启动；HTTP 附件上传和下载不应启动容器。
- 用户连续空闲 10 分钟后停止容器，连续空闲 1 小时后删除容器，但始终保留用户 Volume。
- 再次执行任务时，使用原 Volume 重建或启动容器，用户文件不需要重新复制或挂载。
- 全局限制同时运行的容器数量。达到上限时优先停止最久未使用且没有活跃任务的容器；如果所有容器都在执行，则新任务排队。
- 当前使用普通 Docker。它适合可信内部用户和第一版部署，但不作为面对恶意公网用户的强安全边界。
- 当前不使用 MinIO。未来多节点部署时，可以将 MinIO 作为持久对象存储，通过显式同步进入本地工作区，但不直接以文件系统形式挂载到沙盒。

## 3. 总体架构

```text
                            ┌──────────────────────────┐
HTTP 上传/下载 ────────────▶│ DockerSandboxManager     │
Agent 文件工具/execute ───▶│ 生命周期、并发、权限控制 │
                            └────────────┬─────────────┘
                                         │
                     ┌───────────────────┴───────────────────┐
                     │                                       │
              用户 A 沙盒                            用户 B 沙盒
        ┌──────────────────────┐                ┌──────────────────────┐
        │ Container A（临时）  │                │ Container B（临时）  │
        │ 按需启动/停止/删除   │                │ 按需启动/停止/删除   │
        └──────────┬───────────┘                └──────────┬───────────┘
                   │ /workspace                            │ /workspace
        ┌──────────▼───────────┐                ┌──────────▼───────────┐
        │ Named Volume A       │                │ Named Volume B       │
        │ conversations/       │                │ conversations/       │
        │   会话 1（UID 10001）│                │   会话 1（UID 11001）│
        │   会话 2（UID 10002）│                │   会话 2（UID 11002）│
        └──────────────────────┘                └──────────────────────┘
```

容器是可回收的计算环境，Named Volume 才是用户沙盒文件的持久化载体。删除空闲容器不会删除文件。

## 4. 存储模型

### 4.1 一用户一个 Named Volume

建议保持以下命名方式：

```text
容器：dataagent-sandbox-user-{user_id}
卷：  dataagent-sandbox-user-{user_id}-data
```

每个容器只挂载当前用户自己的 Volume，不得挂载其他用户的目录或 Volume。这样跨用户隔离由 Docker mount namespace 和独立存储卷共同保证。

Named Volume 由 Docker 管理。容器停止、删除或按照新镜像重建后，Volume 仍然存在；新容器重新挂载相同 Volume 即可恢复该用户的所有会话文件。

### 4.2 Volume 内部目录

```text
/workspace/
├── conversations/
│   ├── {conversation_id_1}/
│   │   ├── .home/
│   │   ├── .cache/
│   │   ├── .tmp/
│   │   ├── uploads/
│   │   └── outputs/
│   └── {conversation_id_2}/
├── .dataagent-staging/
└── .dataagent-uids.json
```

- `/workspace/conversations` 归 `root:root` 所有，权限为 `0711`。
- 每个会话目录使用独立 UID/GID，权限为 `0700`。
- 会话内的 `HOME`、缓存和临时文件必须位于本会话目录中，避免不同会话共享用户级缓存。
- `.dataagent-staging` 只允许受信任的管理逻辑使用，不能暴露给 Agent 命令。

### 4.3 会话 UID

为每个 `(user_id, conversation_id)` 分配稳定且不重复的 Linux UID：

```text
conversation A -> uid=10001, gid=10001
conversation B -> uid=10002, gid=10002
```

会话命令必须通过以下方式运行：

```text
user={conversation_uid}:{conversation_uid}
workdir=/workspace/conversations/{conversation_id}
```

UID 映射由沙盒管理器写入用户 Volume 中的受信任注册表 `/workspace/.dataagent-uids.json`。注册表归 `root:root` 所有、权限为 `0600`，Agent 进程不能读取或修改。分配 UID 时检查注册表中的全部已用值，容器删除和重建后仍使用原 UID，确保已有文件属主保持一致。

UID 和 `0700` 是同一用户不同会话之间的实际安全边界。仅在应用层解析命令并禁止 `..` 不足以形成隔离，因为 shell 变量、重定向、脚本和符号链接都可能绕过字符串检查。

## 5. 容器生命周期

### 5.1 状态

一个用户沙盒具有以下状态：

```text
不存在 ──创建──▶ 已停止 ──执行任务──▶ 运行中
   ▲                 ▲                   │
   │                 └──空闲 10 分钟────┘
   └────────空闲 1 小时后删除容器────────┘
```

这里的“删除”只删除容器，不删除 Named Volume。只有用户删除账号、管理员清理数据或明确调用“删除用户沙盒数据”时，才允许删除 Volume。

### 5.2 活跃操作租约

每次需要运行容器的操作都必须持有用户级执行租约：

1. 获取用户生命周期锁。
2. 等待全局运行槽位。
3. 创建或启动用户容器。
4. 增加 `active_operation_count` 并更新 `last_activity_at`。
5. 执行命令。
6. 在 `finally` 中减少计数并再次更新活动时间。

只有 `active_operation_count == 0` 时，清理任务才可以停止或删除容器。这样可以避免 TTL 清理与正在执行的 Agent 任务竞争。

### 5.3 TTL 清理

目标配置如下：

```yaml
sandbox:
  idle_stop_seconds: 600
  idle_remove_seconds: 3600
  cleanup_interval_seconds: 60
  max_running_containers: 8 # 当前默认值，按部署资源调整
```

清理规则：

- 空闲不足 10 分钟：不处理。
- 空闲达到 10 分钟：停止运行中的容器，保留容器和 Volume。
- 空闲达到 1 小时：删除容器，保留 Volume。
- 发现容器镜像或安全配置发生变化：在没有活跃操作时删除旧容器，下次使用时按新规格重建。
- 服务关闭：停止本服务管理的运行中容器，不删除 Volume。

后端对象使用稳定的用户/会话标识，不永久缓存某个 Docker container 实例。每次操作都通过管理器重新解析当前容器，因此能够适应 TTL 删除和容器重建。

## 6. 文件上传与下载

### 6.1 HTTP 附件接口

HTTP 上传和下载不应调用 `docker exec`，也不应为了传输文件启动用户容器。

- 上传：使用 Docker Archive API 将 tar 内容写入已停止容器挂载的 Volume。
- 下载：使用 Docker Archive API 从已停止容器读取文件。
- 新建会话目录时，archive 条目必须设置正确的 UID/GID 和权限。
- 路径只能由服务端根据 `user_id` 和 `conversation_id` 生成，不接受客户端提供真实容器路径。
- 上传前后校验单文件大小和会话工作区容量。
- 禁止绝对路径、`..`、反斜杠、控制字符和符号链接穿透。

如果用户容器已因空闲 1 小时被删除，可以创建一个保持停止状态的新容器并挂载原 Volume，再执行 Archive API 操作；不需要启动容器进程。

### 6.2 Agent 文件工具

需要区分 HTTP 附件接口和 Agent 内部文件工具：

- HTTP 上传/下载只负责外部文件传输，不启动容器。
- Agent 的 `read`、`write`、`grep`、`glob`、`execute` 等工具可能依赖沙盒命令，因此可以按执行任务处理并启动容器。

“上传下载不启动容器”不意味着 Agent 执行期间的所有文件操作都必须脱离容器。

## 7. 并发与容量控制

约 1500 个并发 Agent 不等于 1500 个同时运行的沙盒容器。大量 Agent 可能正在等待模型、数据库或其他工具，真正需要容器资源的是同时执行本地代码的任务。

管理器需要提供以下控制：

- `max_running_containers`：全局运行容器上限。
- 用户级生命周期锁：串行化同一用户容器的创建、启动、停止和删除。
- 用户级活跃计数：阻止清理活跃容器。
- 会话级写锁：避免同一会话同时上传、编辑或删除同一个工作区。
- 全局等待队列：所有运行槽位都被活跃任务占用时，新任务排队。
- LRU 回收：达到上限时，优先停止最久未使用且没有活跃任务的容器。

不能根据 Agent 并发量直接设置容器上限。应根据单机可用 CPU、内存和任务特征压测确定：

```text
max_running_containers <= min(
    可分配内存 / 单容器实际峰值内存,
    可分配 CPU / 单任务目标 CPU,
    运维允许的 Docker 并发规模
)
```

每个容器仍需配置独立的内存、CPU、PID、文件大小、工作区容量、命令执行时间和输出捕获上限。

## 8. 安全边界

容器运行时至少保持以下限制：

- 非 root 身份执行 Agent 命令。
- 根文件系统只读，仅 `/workspace` 和受控临时目录可写。
- `cap_drop: [ALL]`。
- `no-new-privileges`。
- 不挂载 Docker socket、宿主机目录或其他用户 Volume。
- 默认 `network_mode: none`；确实需要网络时通过受控代理和白名单提供。
- 设置内存、CPU、PID、超时、输出和磁盘容量限制。
- 管理操作和 Agent 命令分开：只有受信任管理逻辑可以创建目录、调整 UID 或删除会话。

普通 Docker 仍然共享宿主机内核，因此这个方案属于工程上可接受的容器隔离，不等同于虚拟机安全边界。若未来开放给恶意或完全不可信的公网用户，应重新评估 gVisor、Kata Containers、微虚拟机或独立执行集群。

## 9. 为什么当前不使用 MinIO

MinIO 是对象存储，不是完整的 POSIX 文件系统。通过 FUSE/S3FS 将它直接挂载进容器会引入额外复杂度：

- rename、append、随机写、文件锁等语义与本地文件系统不同。
- 数据分析工具通常会频繁读取小文件、临时文件和中间结果，直接挂载对象存储的性能与稳定性不理想。
- FUSE 挂载需要额外权限和运行组件，会扩大沙盒攻击面。
- 缓存、一致性、失败恢复和并发写入更难处理。

当前单机部署使用 Named Volume 更简单，也更符合 Python、DuckDB、Pandas 和常见命令行工具对本地文件系统的预期。

未来切换到多节点时，可以采用以下模型：

```text
MinIO（持久对象源）
        │ 显式下载/上传
        ▼
节点本地临时工作区或缓存
        │ bind/volume
        ▼
短生命周期执行容器
```

即 MinIO 负责持久化和跨节点分发，容器仍然操作本地 POSIX 工作区，不直接挂载 MinIO。

## 10. 当前不处理的范围

- 多个 FastAPI worker 之间的分布式锁、运行槽位和活动时间同步。
- 多节点 Docker 调度、共享存储和用户到节点的粘性路由。
- gVisor、NsJail、Kata Containers 或微虚拟机。
- MinIO 对象同步、版本管理和垃圾回收。
- 面向恶意公网用户的强多租户安全承诺。

当前实现按单个应用进程、单个 Docker 主机设计。启用多个进程或扩展到多节点之前，必须先补充集中式租约和调度机制。

## 11. 落地状态

截至本文编写时，代码与目标方案的主要对应关系如下：

| 能力 | 当前状态 | 说明 |
| --- | --- | --- |
| 每用户一个容器和 Named Volume | 已完成 | 容器可回收，Volume 独立持久化 |
| 每会话独立 UID/GID 和 `0700` | 已完成 | UID 注册表持久化在用户 Volume 中 |
| 独立 `HOME`、缓存、`TMPDIR` 和 `umask 077` | 已完成 | 默认文件权限为 `0600` |
| 非 root、只读根文件系统、能力移除、资源限制 | 已完成 | 已有真实 Docker 安全测试 |
| 默认禁止沙盒网络 | 已完成 | `network_mode: none` |
| 空闲 10 分钟停止容器 | 已完成 | `idle_stop_seconds: 600` |
| 空闲 1 小时删除容器并保留 Volume | 已完成 | 缓存 Backend 可自动重建容器 |
| HTTP 上传/下载不启动容器 | 已完成 | 使用 stopped-container Archive API |
| 全局运行容器上限、排队和 LRU 回收 | 已完成 | 当前默认上限为 8 |
| 后端适应容器被删除和重建 | 已完成 | 操作时动态解析容器 |
| 多进程/多节点协调 | 暂不实现 | 扩容前重新设计 |

## 12. 已实施内容

1. 空闲配置已经拆分为 `idle_stop_seconds` 和 `idle_remove_seconds`。
2. Backend 已改为按操作动态解析容器，可适应停止、删除和重建。
3. 附件上传、下载和存在性检查已经从启动容器的路径中拆分。
4. 清理任务会在空闲 10 分钟后停止、1 小时后删除容器并保留 Volume。
5. 已增加全局运行槽位、等待队列和空闲容器 LRU 回收。
6. 会话 UID 注册表会随 Volume 持久化，并拒绝重复或非法 UID。
7. 已增加生命周期竞争、停止状态文件传输、容器重建、排队和跨会话越权测试。

## 13. 验收条件

- [x] 两个用户不会挂载同一个用户 Volume。
- [x] 同一用户的会话 A 无法通过 shell、符号链接或路径跳转读取会话 B 的 `0700` 目录。
- [x] 停止用户容器后仍可通过 HTTP 上传和下载附件，且操作不会使容器进入运行状态。
- [x] 有活跃命令时，容量回收和 TTL 清理不会停止或删除容器。
- [x] 空闲 10 分钟后容器变为 stopped，文件仍存在。
- [x] 空闲 1 小时后容器被删除，Volume 仍存在；再次执行时文件可恢复。
- [x] 达到 `max_running_containers` 后不会继续无上限创建运行中容器，新任务会排队或回收安全的空闲容器。
- [x] 容器镜像或安全配置更新后能够重建容器，同时保留用户文件。
- [x] 删除单个会话只删除该会话目录；只有显式删除用户沙盒数据才删除整个 Volume。

## 14. 参考资料

- [Docker volumes](https://docs.docker.com/engine/storage/volumes/)
- [Docker storage](https://docs.docker.com/engine/storage/)
- [docker container cp](https://docs.docker.com/reference/cli/docker/container/cp/)
- [MinIO: Filesystem on Object Store Is a Bad Idea](https://blog.min.io/filesystem-on-object-store-is-a-bad-idea/)
