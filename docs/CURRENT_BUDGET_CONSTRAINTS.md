# 当前预算与资源约束清单

更新时间：2026-08-29

处理结果用于后续填写：`保留`、`调整`、`删除`。

## 1. Agent 编排

| ID    | 约束项                         |                                  当前值 | 作用域                | 定义位置                                  | 处理结果 |
| ----- | ------------------------------ | --------------------------------------: | --------------------- | ----------------------------------------- | -------- |
| AG-01 | `max_parallel_sessions`        |                                       8 | Conversation Runtime  | `conf/app_config.yaml`                    | 待定     |
| AG-02 | `max_continuations`            |                                       3 | 用户回合              | `conf/app_config.yaml`                    | 待定     |
| AG-03 | 同一 Conversation Planner 并发 |                                       1 | Conversation          | `app/analytics/agents/manager.py`         | 待定     |
| AG-04 | 同一 Session 执行并发          |                                       1 | Session               | `app/analytics/agents/session_service.py` | 待定     |
| AG-06 | Specialist 结构化修正次数      |                                       1 | Delegation            | `app/analytics/agents/session_service.py` | 待定     |
| AG-07 | 连续重复 Repair Fingerprint    |                              1 次后终止 | Session / Planner Run | `app/analytics/agents/session_service.py` | 待定     |
| AG-08 | Conversation Runtime 缓存      |                                     128 | API 进程              | `app/analytics/agents/manager.py`         | 待定     |

### AG-06 Specialist 结构化修正次数

Specialist 首次返回的结构化结果出现以下问题时，服务端允许追加一次修正请求：

- 无法解析为 `SpecialistResult`。
- `completed` 缺少 findings 或 artifacts。
- `needs_repair` 缺少 Repair Request。
- Repair Request 指向自身或不存在的 Session。
- artifact/evidence 路径越界。
- artifact/evidence 文件不存在。

修正请求要求 Specialist 根据已有工作重新输出结构化结果，并避免重新执行工具：

```text
首次结果生成
→ 校验失败
→ 1 次结构化修正
→ 再次校验
```

第二次结果仍然无效时，当前 delegation 返回 `failed`。专业 Agent 执行过程直接抛出的异常不会触发这次结构化修正。

### AG-07 连续重复 Repair Fingerprint

用于阻止同一个 Specialist Session 在一个 Planner Run 中重复提出相同修补要求。Fingerprint 包含：

- `target_agent_type`
- `target_session_id`
- `reason`
- evidence 文件路径

`expected_result` 当前不参与 Fingerprint。

```text
第一次返回 Repair A → 接受
再次返回完全相同的 Repair A → 转换为 failed
```

该状态按源 Session 和 Planner Run 保存。不同源 Session 独立判断；下一条用户消息开始时重置；删除源 Session 时清除。中间返回 `completed` 不会清除上一次 Repair Fingerprint，返回不同的 Repair Fingerprint 会替换上一次记录。

### AG-08 Conversation Runtime 缓存

每个 API 进程最多缓存 128 个 Conversation Agent Runtime。缓存内容包括编译后的 Planner、SessionService、Session 进程内锁、活跃 Session 状态和 Conversation Backend 等运行时对象。

缓存采用 LRU 顺序。访问 Conversation 后，对应 Runtime 移动到缓存末尾；超过 128 时，优先清理最久未使用且当前没有执行任务的 Runtime。

Runtime 被清理后，PostgreSQL Checkpoint、Conversation 沙箱和 Session 工作区继续保留。再次访问该 Conversation 时重新构建 Runtime。如果超过 128 时所有旧 Runtime 都正在执行，缓存允许暂时超过上限，避免驱逐活跃对象。

## 2. Planner QuickJS

| ID    | 约束项                   | 当前值 | 作用域    | 定义位置               | 处理结果 |
| ----- | ------------------------ | -----: | --------- | ---------------------- | -------- |
| JS-01 | `timeout_seconds`        |  30 秒 | 单次 eval | `conf/app_config.yaml` | 待定     |
| JS-02 | `memory_limit_bytes`     | 64 MiB | 单次 eval | `conf/app_config.yaml` | 待定     |
| JS-03 | `max_ptc_calls_per_eval` |     30 | 单次 eval | `conf/app_config.yaml` | 待定     |

## 3. 模型

| ID    | 约束项                      |    当前值 | 作用域       | 定义位置                         | 处理结果 |
| ----- | --------------------------- | --------: | ------------ | -------------------------------- | -------- |
| LM-01 | 模型请求超时                |     30 秒 | 单次模型请求 | `app/analytics/model_factory.py` | 待定     |
| LM-02 | 模型请求重试                |         2 | 单次模型调用 | `app/analytics/model_factory.py` | 待定     |
| LM-03 | DeepSeek `max_input_tokens` | 1,048,576 | 模型上下文   | `conf/app_config.yaml`           | 待定     |
| LM-04 | Qwen `max_input_tokens`     | 1,000,000 | 模型上下文   | `conf/app_config.yaml`           | 待定     |

## 4. SpecialistResult 协议

| ID    | 约束项                          |      当前值 | 作用域           | 定义位置                            | 处理结果 |
| ----- | ------------------------------- | ----------: | ---------------- | ----------------------------------- | -------- |
| PR-01 | 单条文本最大长度                | 20,000 字符 | 协议字段         | `app/analytics/agents/contracts.py` | 待定     |
| PR-02 | 单个 Repair Request 的 Evidence |       20 项 | Repair Request   | `app/analytics/agents/contracts.py` | 待定     |
| PR-03 | Findings                        |      100 项 | SpecialistResult | `app/analytics/agents/contracts.py` | 待定     |
| PR-04 | Artifacts                       |      100 项 | SpecialistResult | `app/analytics/agents/contracts.py` | 待定     |
| PR-05 | Repair Requests                 |       20 项 | SpecialistResult | `app/analytics/agents/contracts.py` | 待定     |
| PR-06 | Limitations                     |      100 项 | SpecialistResult | `app/analytics/agents/contracts.py` | 待定     |
| PR-07 | Artifact Path                   |  1,024 字符 | Artifact         | `app/analytics/agents/contracts.py` | 待定     |
| PR-08 | Artifact Media Type             |    255 字符 | Artifact         | `app/analytics/agents/contracts.py` | 待定     |
| PR-09 | Artifact Description            |  2,000 字符 | Artifact         | `app/analytics/agents/contracts.py` | 待定     |

## 5. 聊天与活动流

| ID    | 约束项               |      当前值 | 作用域            | 定义位置                                       | 处理结果 |
| ----- | -------------------- | ----------: | ----------------- | ---------------------------------------------- | -------- |
| TX-01 | 子 Agent Tool Args   | 20,000 字符 | 单条 SSE 消息     | `app/analytics/services/chat.py`               | 待定     |
| TX-02 | 子 Agent Tool Result | 50,000 字符 | 单条 SSE 消息     | `app/analytics/services/chat.py`               | 待定     |
| TX-03 | 初始标题消息         | 20,000 字符 | 创建 Conversation | `app/analytics/api/chat/schemas.py`            | 待定     |
| TX-04 | Conversation 标题    |     64 字符 | Conversation      | `app/analytics/api/chat/schemas.py`            | 待定     |
| TX-05 | 标题模型输入         |  4,000 字符 | 标题生成          | `app/analytics/services/conversation_title.py` | 待定     |
| TX-06 | 生成标题长度         |     30 字符 | 标题生成          | `app/analytics/services/conversation_title.py` | 待定     |

## 6. 查询执行

| ID   | 约束项               |     当前值 | 作用域        | 定义位置                         | 处理结果 |
| ---- | -------------------- | ---------: | ------------- | -------------------------------- | -------- |
| Q-01 | `timeout_seconds`    |      60 秒 | 单条 SQL      | `conf/app_config.yaml`           | 待定     |
| Q-02 | `memory_limit_bytes` |      1 GiB | 单条 SQL      | `conf/app_config.yaml`           | 待定     |
| Q-03 | `max_rows`           | 100,000 行 | 单条 SQL      | `conf/app_config.yaml`           | 待定     |
| Q-04 | `max_output_bytes`   |     32 MiB | 单条 SQL 输出 | `conf/app_config.yaml`           | 待定     |
| Q-05 | `batch_size`         |     100 行 | 查询拉取      | `conf/app_config.yaml`           | 待定     |
| Q-06 | `sample_rows`        |       5 行 | 查询摘要      | `conf/app_config.yaml`           | 待定     |
| Q-07 | Sample 字符串        |   512 字符 | 查询摘要字段  | `app/query/services/executor.py` | 待定     |
| Q-08 | Sample 集合          |      20 项 | 查询摘要字段  | `app/query/services/executor.py` | 待定     |
| Q-09 | Sample 嵌套深度      |          4 | 查询摘要字段  | `app/query/services/executor.py` | 待定     |

## 7. 沙箱

### 7.1 单个 Conversation 沙箱

| ID    | 约束项         |  当前值 | 作用域              | 定义位置               | 处理结果 |
| ----- | -------------- | ------: | ------------------- | ---------------------- | -------- |
| SB-01 | 容器内存       |   2 GiB | 沙箱容器            | `conf/app_config.yaml` | 待定     |
| SB-02 | CPU            |    2 核 | 沙箱容器            | `conf/app_config.yaml` | 待定     |
| SB-03 | PID            |     256 | 沙箱容器            | `conf/app_config.yaml` | 待定     |
| SB-04 | Shell 执行超时 |  120 秒 | 单条命令            | `conf/app_config.yaml` | 待定     |
| SB-05 | 命令返回输出   |   4 MiB | 单条命令            | `conf/app_config.yaml` | 待定     |
| SB-06 | 捕获内容       |  10 MiB | 单次读取/捕获       | `conf/app_config.yaml` | 待定     |
| SB-07 | 单文件         | 100 MiB | Conversation 工作区 | `conf/app_config.yaml` | 待定     |
| SB-08 | 工作区         |   1 GiB | Conversation 工作区 | `conf/app_config.yaml` | 待定     |

### 7.2 全局容量

| ID    | 约束项       | 当前值 | 作用域       | 定义位置               | 处理结果 |
| ----- | ------------ | -----: | ------------ | ---------------------- | -------- |
| SB-09 | 运行容器     |      8 | 部署实例     | `conf/app_config.yaml` | 待定     |
| SB-10 | 容量等待请求 |  2,048 | 部署实例     | `conf/app_config.yaml` | 待定     |
| SB-11 | 容量等待超时 | 300 秒 | 单个等待请求 | `conf/app_config.yaml` | 待定     |

### 7.3 所有权和生命周期

| ID    | 约束项                 |   当前值 | 作用域     | 定义位置               | 处理结果 |
| ----- | ---------------------- | -------: | ---------- | ---------------------- | -------- |
| SB-12 | Ownership Lock Timeout |    60 秒 | 沙箱所有权 | `conf/app_config.yaml` | 待定     |
| SB-13 | Ownership Wait Timeout |   300 秒 | 沙箱所有权 | `conf/app_config.yaml` | 待定     |
| SB-14 | Ownership Lease        |    30 秒 | 沙箱所有权 | `conf/app_config.yaml` | 待定     |
| SB-15 | Idle Stop              |   600 秒 | 沙箱容器   | `conf/app_config.yaml` | 待定     |
| SB-16 | Idle Remove            | 3,600 秒 | 沙箱容器   | `conf/app_config.yaml` | 待定     |
| SB-17 | Cleanup Interval       |    60 秒 | 部署实例   | `conf/app_config.yaml` | 待定     |
| SB-18 | Cleanup Failure Alert  |     3 次 | 部署实例   | `conf/app_config.yaml` | 待定     |

## 8. 外部客户端和连接池

| ID    | 约束项                  |   当前值 | 作用域          | 定义位置                                        | 处理结果 |
| ----- | ----------------------- | -------: | --------------- | ----------------------------------------------- | -------- |
| CL-01 | Embedding 请求超时      |    30 秒 | 单次请求        | `conf/app_config.yaml`                          | 待定     |
| CL-02 | PostgreSQL Pool Size    |       10 | 单个 Engine     | `app/shared/clients/postgres_client_manager.py` | 待定     |
| CL-03 | PostgreSQL Max Overflow |       20 | 单个 Engine     | `app/shared/clients/postgres_client_manager.py` | 待定     |
| CL-04 | PostgreSQL Pool Timeout |    30 秒 | 单次连接等待    | `app/shared/clients/postgres_client_manager.py` | 待定     |
| CL-05 | PostgreSQL Pool Recycle | 1,800 秒 | 单条连接        | `app/shared/clients/postgres_client_manager.py` | 待定     |
| CL-06 | Doris Pool Size         |       10 | 单个角色 Engine | `app/shared/clients/doris_client_manager.py`    | 待定     |
| CL-07 | Doris Max Overflow      |       20 | 单个角色 Engine | `app/shared/clients/doris_client_manager.py`    | 待定     |
| CL-08 | Doris Pool Timeout      |    30 秒 | 单次连接等待    | `app/shared/clients/doris_client_manager.py`    | 待定     |
| CL-09 | Doris Pool Recycle      | 1,800 秒 | 单条连接        | `app/shared/clients/doris_client_manager.py`    | 待定     |

## 9. 认证

| ID    | 约束项            |      当前值 | 作用域        | 定义位置                              | 处理结果 |
| ----- | ----------------- | ----------: | ------------- | ------------------------------------- | -------- |
| AU-01 | 登录 IP           | 30 次/60 秒 | 单进程 / IP   | `app/identity/services/rate_limit.py` | 待定     |
| AU-02 | 登录账号标识      | 10 次/60 秒 | 单进程 / 标识 | `app/identity/services/rate_limit.py` | 待定     |
| AU-03 | Refresh IP        | 60 次/60 秒 | 单进程 / IP   | `app/identity/services/rate_limit.py` | 待定     |
| AU-04 | IP 限流键         |      10,000 | 单进程        | `app/identity/services/rate_limit.py` | 待定     |
| AU-05 | 账号限流键        |      50,000 | 单进程        | `app/identity/services/rate_limit.py` | 待定     |
| AU-06 | Argon2 并发       |           4 | API 进程      | `app/identity/services/auth.py`       | 待定     |
| AU-07 | Access Token TTL  |     15 分钟 | Token         | `conf/app_config.yaml`                | 待定     |
| AU-08 | Refresh Token TTL |       30 天 | Token         | `conf/app_config.yaml`                | 待定     |

## 10. Celery

### 10.1 全局

| ID    | 约束项             |    当前值 | 作用域         | 定义位置                         | 处理结果 |
| ----- | ------------------ | --------: | -------------- | -------------------------------- | -------- |
| BG-01 | Soft Time Limit    |  3,300 秒 | 单个任务       | `conf/app_config.yaml`           | 待定     |
| BG-02 | Hard Time Limit    |  3,600 秒 | 单个任务       | `conf/app_config.yaml`           | 待定     |
| BG-03 | Visibility Timeout |  3,900 秒 | Broker 消息    | `app/shared/tasks/celery_app.py` | 待定     |
| BG-04 | Result Expiry      | 86,400 秒 | 任务结果       | `conf/app_config.yaml`           | 待定     |
| BG-05 | Worker Prefetch    |         1 | Worker Process | `conf/app_config.yaml`           | 待定     |
| BG-06 | Task Publish Retry |         0 | 任务提交       | `app/shared/tasks/celery_app.py` | 待定     |

### 10.2 批处理和调度

| ID     | 约束项                           |     当前值 | 定义位置                | 处理结果 |
| ------ | -------------------------------- | ---------: | ----------------------- | -------- |
| BG-B01 | 草稿 TTL                         | 1,440 分钟 | `conf/app_config.yaml`  | 待定     |
| BG-B02 | Lifecycle Cleanup Batch          |        100 | `conf/app_config.yaml`  | 待定     |
| BG-B03 | Lifecycle Schedule               |     300 秒 | `conf/app_config.yaml`  | 待定     |
| BG-B04 | User Deletion Retry Schedule     |      60 秒 | `conf/app_config.yaml`  | 待定     |
| BG-B05 | Query Experience Repair Batch    |        500 | `app/query/tasks.py`    | 待定     |
| BG-B06 | Query Experience Repair Schedule |     300 秒 | `conf/app_config.yaml`  | 待定     |
| BG-B07 | Metadata Periodic Batch          |        500 | `app/metadata/tasks.py` | 待定     |
| BG-B08 | Value Index Lookback             |     300 秒 | `conf/app_config.yaml`  | 待定     |
