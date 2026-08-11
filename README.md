# 使用方式
## 启动容器
- 准备 `docker/elasticsearch`
- 执行 `docker compose up -d`

## 在数据库中建表并导入数据
- 元数据存储在 PostgreSQL 的 `meta` 数据库
- 会话目录和 Agent 状态存储在 PostgreSQL 的 `langgraph` 数据库
- 业务数据存储在 Doris，通过 FE 的 MySQL 协议端口 `9030` 查询
- 业务数据准备由 dbmock 项目负责

## 修改配置信息
- 编辑 [conf/app_config.yaml](conf/app_config.yaml)，配置数据库和模型信息
- 编辑 [`conf/.env`](conf/.env)，配置模型服务密钥
- `doris` 配置指向 Doris
- `meta_postgresql` 配置指向 PostgreSQL 的 `meta` 数据库
- `langgraph_postgresql` 配置指向 PostgreSQL 的 `langgraph` 数据库
- 对话目录由 LangGraph PostgreSQL Store 持久化，对话消息和 Agent 执行状态由 PostgreSQL Checkpointer 持久化
- PostgreSQL 容器首次初始化数据卷时自动创建 `meta` 和 `langgraph` 数据库
- PostgreSQL 的 LangGraph 和元数据表由应用启动时自动初始化
- 已有 PostgreSQL 实例可执行 `uv run scripts/init_db.py` 创建缺失的数据库
- [conf/meta_config.yaml](conf/meta_config.yaml) 是元数据导入文件示例
- Doris `UNIQUE KEY` 字段作为表的逻辑主键自动读取，不需要在 YAML 或接口请求中填写
- 字段类型和示例值从业务数据库自动读取，不需要在 YAML 或接口请求中填写
- 字段的 `index_values` 控制是否建立字段值索引
- 表名是表元数据主键，表名和字段名共同组成字段元数据联合主键
- 外键字段通过 `reference_t_name` 和 `reference_c_name` 指向目标字段
- 指标名是指标元数据主键，`relevant_columns` 使用表名和字段名关联字段
- `meta_version` 在元数据内容变化时递增，字段和指标的 `index_version` 记录语义索引使用的元数据版本
- `value_index_synced_at` 记录字段值索引最近同步成功时间，`value_index_sync_status` 记录最近同步状态；源表数据变化后仍需手动重新同步字段值索引
- 新元数据的版本为 `1`，新字段和指标的各索引版本为 `0`，版本由服务端维护，不需要写入 YAML

## 启动
```bash
uv sync         # 安装依赖
uv run main.py  # 启动服务
```

## 导入和导出元数据
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/meta/import?mode=merge" \
  -F "file=@conf/meta_config.yaml"

curl -X POST \
  "http://127.0.0.1:8000/api/v1/meta/import?mode=replace" \
  -F "file=@conf/meta_config.yaml"

curl "http://127.0.0.1:8000/api/v1/meta/export" -o metadata.yaml
```

## 同步语义索引

字段和指标的全文内容与向量统一存储在 Elasticsearch。首次部署或从 Qdrant
迁移后，需要根据 PostgreSQL 元数据重新同步语义索引，旧 Qdrant 数据不会自动复制。

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/meta/columns/sync" \
  -H "Content-Type: application/json" \
  -d '{"columns":[{"t_name":"orders","c_name":"amount"}]}'

curl -X POST "http://127.0.0.1:8000/api/v1/meta/metrics/sync" \
  -H "Content-Type: application/json" \
  -d '{"metrics":["GMV"]}'
```
