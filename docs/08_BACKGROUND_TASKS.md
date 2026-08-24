# Celery 后台任务与可靠性设计

## 1. 运行组件

后台任务使用 Celery 5.6，Redis 同时承担 Broker 和短期结果后端：

```text
FastAPI ──发布任务──> Redis DB 0 ──消费──> Celery Worker
前端 ──查询状态──> FastAPI ──读取──> Redis DB 1
Celery Beat ──周期触发──> Redis DB 0
```

[`docker/compose.yml`](../docker/compose.yml) 使用 `redis:8.10.0-alpine`，开启 AOF 持久化并挂载 `redis-data` 数据卷。任务结果默认保留 24 小时，业务事实、同步版本和删除墓碑仍保存在 PostgreSQL 或 LangGraph Store 中。

## 2. 队列划分

| 队列 | 任务 | 资源特征 |
| :--- | :--- | :--- |
| `metadata-index` | 字段/指标语义索引、字段取值索引、YAML 导入、查询经验索引 | Embedding、Doris、PostgreSQL、Elasticsearch I/O |
| `lifecycle` | 会话物理清理、过期草稿清理、用户注销 | PostgreSQL、LangGraph、Elasticsearch、Docker 跨存储操作 |
| `lightweight` | 会话标题生成 | 单次模型调用和 LangGraph Store 条件更新 |
| `default` | 未显式路由的管理任务 | 兜底队列 |

所有任务使用 JSON 序列化。长任务开启 late acknowledgement、Worker 异常退出重投、软硬超时和单任务预取，降低 Worker 崩溃造成的任务丢失与长任务饥饿。

## 3. 已迁移场景

| 场景 | 请求内操作 | Worker 操作 | 可靠性依据 |
| :--- | :--- | :--- | :--- |
| 元数据语义/取值索引 | 元数据更新后自动提交语义任务，管理员手动提交取值全量任务 | 语义文档差量、Embedding、Doris 水位扫描、ES 写入、版本和水位更新 | 语义任务使用 Celery 重试；取值任务使用 `value_index_sync_state` 和每日 Beat 增量调度 |
| 查询经验索引 | PostgreSQL 记录成功事实 | Embedding 和 ES 投影维护 | `revision/indexed_revision` 定期补偿 |
| YAML 元数据导入 | YAML 解析和结构校验 | Doris 校验、批量写库、索引清理 | Celery 重试和任务结果 |
| 用户注销 | 禁用用户、吊销令牌、写注销任务表 | 清理会话、沙盒、查询历史和用户记录 | `user_deletion_tasks` 到期重投 |
| 会话删除 | 取消当前进程执行并写删除墓碑 | 清理 Checkpoint、召回记录、沙盒目录和会话目录 | 删除墓碑定期扫描补偿 |
| 过期草稿 | 无请求操作 | 周期扫描并执行跨存储删除 | Celery Beat 周期触发 |
| 会话标题 | 保存首条消息和待生成状态 | 调用模型并条件更新标题 | 待生成记录定期重投，避免覆盖手工标题 |

## 4. 启动方式

先启动 Redis，再分别启动 API、Worker 和 Beat：

```bash
docker compose -f docker/compose.yml up -d redis
uv sync --group dev
make run
make worker
make beat
```

Worker 消费全部业务队列，并且需要访问与 API 相同的 Docker Engine 和沙盒数据卷。Beat 只能运行一个实例，避免同一周期任务被重复发布。

## 5. API 与前端轮询

索引同步和实际 YAML 导入接口返回 `task_id`。管理员通过 `GET /api/v1/tasks/{task_id}` 查询 `PENDING`、`STARTED`、`SUCCESS` 或 `FAILURE` 状态。前端 API 层统一轮询该接口，任务成功后返回原有同步统计或导入差异，页面交互保持一致。

任务状态接口只对平台管理员开放。Redis 中的任务结果用于短期展示，不能作为业务状态源。

## 6. 补偿与幂等

- 管理员修改字段、指标或通过 YAML 导入元数据后立即提交语义差量同步任务，语义索引不进行 Beat 定期扫描
- 取值索引 Beat 按 `task_queue.value_index_sync_time` 指定的 `Asia/Shanghai` 本地时间每天运行一次，只领取已经完成手动全量构建且具有可靠游标的字段
- 取值索引首次构建和全量校准只能由管理员手动触发，未配置游标的字段不参与每日增量任务
- 取值索引任务提交前会在 PostgreSQL 中领取字段，避免重复发布仍在排队或运行的水位任务；发布失败会立即释放为失败状态
- 查询经验 Beat 扫描 `indexed_revision < revision` 的记录并重新提交
- 用户注销 Beat 扫描 `user_deletion_tasks.next_attempt_at` 到期记录
- 会话生命周期 Beat 扫描删除墓碑和过期草稿
- 标题修复 Beat 扫描长时间处于待生成状态的会话
- 语义文档和字段取值使用稳定主键，语义索引只更新差异文档，取值索引通过水位回看与全量代次支持重复执行
- PostgreSQL 版本只在目标投影完成后推进，失败的语义任务可由管理员重新保存元数据或手动同步

Redis 发布瞬时失败时，查询经验、用户注销和生命周期任务可由各自的持久状态与 Beat 恢复。语义索引不做周期扫描，管理员更新接口或手动同步接口提交失败时会直接返回错误，调用方需要重新发起。
