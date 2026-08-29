## 7. 沙箱
| SB-04 | Shell 执行超时 |  120 秒 | 单条命令            | `conf/app_config.yaml` | 待定     |

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
