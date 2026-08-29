## 9. 认证

| ID    | 约束项       |      当前值 | 作用域        | 定义位置                              | 处理结果 |
| ----- | ------------ | ----------: | ------------- | ------------------------------------- | -------- |
| AU-01 | 登录 IP      | 30 次/60 秒 | 单进程 / IP   | `app/identity/services/rate_limit.py` | 待定     |
| AU-02 | 登录账号标识 | 10 次/60 秒 | 单进程 / 标识 | `app/identity/services/rate_limit.py` | 待定     |
| AU-03 | Refresh IP   | 60 次/60 秒 | 单进程 / IP   | `app/identity/services/rate_limit.py` | 待定     |
| AU-04 | IP 限流键    |      10,000 | 单进程        | `app/identity/services/rate_limit.py` | 待定     |
| AU-05 | 账号限流键   |      50,000 | 单进程        | `app/identity/services/rate_limit.py` | 待定     |
| AU-06 | Argon2 并发  |           4 | API 进程      | `app/identity/services/auth.py`       | 待定     |

## 10. Celery

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
