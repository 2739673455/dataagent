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
