# 模块五：Docker 多租户沙盒运行环境

## 1. 模块定位与职责

Docker 多租户沙盒运行环境是 DataAgent 专业 Agent 执行代码、处理大型数据集与持久化分析产物的安全隔离执行层。该模块通过 Docker 容器与 Named Volume，为每个用户提供独立、持久、可回收且受配额限制的 Linux 工作空间，确保多用户、多会话并发执行互不干扰。

```mermaid
flowchart TD
    subgraph Host [FastAPI 应用宿主]
        Manager[DockerSandboxManager\n生命周期 / 并发调度 / 配额控制]
        Attachment[AttachmentRouter\n附件上传 / 下载]
    end

    subgraph FIFO [容器容量调度]
        Queue[有界 FIFO 任务队列\n支持超时 / 取消]
    end

    subgraph UserSandbox [用户级 Docker 隔离环境]
        Container[User Container（按需启动 / 自动回收）\n执行 Agent 统计分析脚本]
        Volume[(Named Volume 持久卷\n数据长期保留)]
    end

    Manager --> FIFO
    FIFO --> UserSandbox
    Attachment -->|直接操作卷/工作区| Manager
    Container -->|挂载 /workspace| Volume

    subgraph VolumeDir [Volume 内部目录划分]
        Conv1[/workspace/conversations/conv-1\nUID 10001 / 0700 权限]
        Conv2[/workspace/conversations/conv-2\nUID 10002 / 0700 权限]
    end

    Volume --> VolumeDir
```

---

## 2. 核心架构与设计机制

### 2.1 一用户一容器与一用户一持久卷
- **存储载体（Named Volume）**：
  - 命名格式：`dataagent-{deployment_namespace}-sandbox-user-{user_id}-data`。
  - 用户的全部会话文件、生成的数据集、Python 脚本和可视化产物统一保存在该持久卷中。
  - **容器与卷解耦**：容器是可随时销毁重建的无状态计算资源，Named Volume 才是持久化载体；销毁容器不会导致用户文件丢失。
- **计算容器（Container）**：
  - 命名格式：`dataagent-{deployment_namespace}-sandbox-user-{user_id}`。
  - 仅挂载当前用户自身的 Named Volume，从根本上杜绝跨用户文件访问。

### 2.2 会话级 UID/GID 权限隔离
在单个用户的 Volume 内部，不同会话之间采用 Linux 权限隔离：
- `/workspace/conversations` 归属于 `root:root`，权限为 `0711`。
- 每个会话分配独立的非特权 UID/GID（如 `10001:10001`），会话子目录权限设定为 `0700`。
- 会话内的 `HOME`、`.cache` 和 `.tmp` 均置于本会话子目录下，阻断不同会话间的缓存共享。

### 2.3 容器生命周期管理与自动回收策略
- **按需启动**：仅当 Agent 需要执行 Shell 命令或运行 Python 脚本时才拉起容器；纯 HTTP 附件读写通过文件流直接处理，不唤醒容器。
- **空闲停止与销毁（TTL）**：
  - 用户连续空闲 **10 分钟** 后，后台任务自动停止容器释放内存与 CPU。
  - 用户连续空闲 **1 小时** 后，自动删除容器实例（保留 Volume）。
  - 活动时间记录在持久卷中的 `.dataagent-activity.json`，服务重启后继续沿用原 TTL。

### 2.4 全局并发控制与有界 FIFO 队列
- **最大运行容器上限**：通过配置限制宿主机上同时处于 `running` 状态的沙盒容器数量。
- **自动腾挪与排队**：
  - 当达到上限且有新任务到达时，优先停止最久未使用且当前无任务的容器。
  - 若所有运行中容器都在执行任务，新任务进入有界 FIFO 队列排队，支持超时退出、任务取消和优雅停机。

### 2.5 工作区容量配额模式
- **`application` 模式**：应用层预检模式。上传、写入和执行前后计算工作区总占用量，配合 `ulimit` 控制单文件大小，适合可信内部部署。
- **`volume_driver` 模式**：在创建 Volume 时将 `max_workspace_bytes` 传递给底层支持硬配额的 Docker Volume Driver，由存储驱动层实施硬隔离。

---

## 3. 核心接口与协议

### 附件与产物接口 (`/api/v1/chat/attachment`)
| 接口 | 方法 | 描述 |
| :--- | :--- | :--- |
| `/upload` | `POST` | 上传文件（CSV、Excel、图片等）至指定会话工作区的 `uploads/` 目录 |
| `/get` | `GET` | 安全获取会话工作区中的文件（支持相对路径校验与 Content-Type 推导） |
| `/delete` | `POST` | 删除指定会话工作区内的用户附件 |

### 会话沙盒后端核心方法
| 方法 | 描述 |
| :--- | :--- |
| `execute(command, timeout=...)` / `aexecute(...)` | 在当前 Agent Session 目录安全执行 Shell/Python 命令 |
| `write(file_path, content)` / `awrite(...)` | 向当前 Session 工作区写入文件 |
| `read(file_path, offset, limit)` / `aread(...)` | 分页读取当前 Session 文件内容 |
| `ls(path)` / `als(path)` | 列出当前 Session 目录内容 |

---

## 4. 关键代码映射

- Docker 沙盒管理器：[`app/clients/docker_sandbox_manager.py`](../app/clients/docker_sandbox_manager.py)
- 附件路由与文件管理：[`app/routes/api/v1/attachment/router.py`](../app/routes/api/v1/attachment/router.py)
- 沙盒配置模型：[`app/conf/app_config.py`](../app/conf/app_config.py)
