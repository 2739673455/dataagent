# DataAgent

## 配置

### 后端环境变量

复制环境变量模板：

```bash
cp conf/.env.example conf/.env
```

编辑 `conf/.env`：

```dotenv
# 本地 docker/compose.yml 的默认密码均为 123123
DORIS_ADMIN_PASSWORD=123123
POSTGRES_PASSWORD=123123

# 分别执行下方命令生成
DORIS_CREDENTIAL_ENCRYPTION_KEY=
JWT_SECRET=

# 外部服务密钥
TAVILY_API_KEY=
SILICONFLOW_API_KEY=
OPENROUTER_API_KEY=
DEEPSEEK_API_KEY=

# 首次创建的平台管理员
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=
```

生成 Doris 凭据加密密钥和 JWT 密钥：

```bash
python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

将两条命令的输出分别复制到 `DORIS_CREDENTIAL_ENCRYPTION_KEY` 和 `JWT_SECRET`。

当前 `conf/app_config.yaml` 引用了以下外部服务环境变量，保留对应配置时需要填写有效密钥：

| 环境变量              | 用途                                       |
| --------------------- | ------------------------------------------ |
| `DEEPSEEK_API_KEY`    | 默认语言模型 `deepseek-deepseek-v4-flash`  |
| `OPENROUTER_API_KEY`  | `app_config.yaml` 中声明的 OpenRouter 模型 |
| `SILICONFLOW_API_KEY` | `BAAI/bge-m3` 文本向量模型                 |
| `TAVILY_API_KEY`      | Tavily MCP 搜索工具                        |

### 应用配置

应用运行参数位于 `conf/app_config.yaml`。本地使用 `docker/compose.yml` 时可直接采用默认配置。连接已有服务或部署多个实例时，重点调整：

- `doris`、`auth_postgresql`、`meta_postgresql`、`langgraph_postgresql`、`elasticsearch`：服务地址、端口、账号和数据库。
- `task_queue`、`auth.rate_limit_redis_url`、`sandbox.ownership.redis_url`：Redis 连接地址。
- `lm_config.active` 与 `lm_config.models`：默认模型及模型服务参数。
- `embedding`：向量模型地址、模型名和向量维度；`elasticsearch.embedding_size` 必须与模型输出维度一致。
- `mcp`：Explorer 可用的 MCP 服务。删除不使用的服务配置后，无需提供对应密钥。
- `sandbox.image`：必须与 Compose 构建的沙箱镜像名一致。
- `sandbox.deployment_namespace`：同一 Docker 主机上的每套部署使用不同值。
- `cors_origins`：前后端跨域部署时加入前端 Origin，例如 `http://localhost:7001`。

### 前端代理

复制前端环境变量模板：

```bash
cp web/.env.example web/.env
```

`web/.env` 默认将 `/api` 代理到本机后端：

```dotenv
VITE_APP_PROXY=http://localhost:7000
```

后端地址变化时修改该值。

## 启动

### 1. 安装依赖

```bash
uv sync
npm --prefix web ci
```

### 2. 启动基础服务

```bash
docker compose -f docker/compose.yml up -d
```

该命令启动 PostgreSQL、Elasticsearch、Redis 和 Doris，并在缺少 `dataagent-sandbox:latest` 时构建沙箱镜像。PostgreSQL 的 `auth`、`meta` 和 `langgraph` 数据库会在首次创建数据卷时自动初始化。

查看服务状态：

```bash
docker compose -f docker/compose.yml ps
```

### 3. 准备 Doris 全量数据

默认应用连接 Doris 的 `ecommerce` 数据库。全量数据依赖 Git LFS 中的数据文件，先在项目根目录拉取：

```bash
git lfs install
git lfs pull
```

创建 `dbmock` 配置并生成两年全量数据：

```bash
cp dbmock/.env.example dbmock/.env
# 将 dbmock/.env 中的 DB_PASSWORD 设置为 123123

cd dbmock
uv sync
uv run scripts/init_db.py
uv run main.py
cd ..
```

`dbmock/scripts/init_db.py` 会删除并重建 `DB_NAME` 指定的数据库，只能用于可重建的本地数据。全量数据生成通常需要十几分钟，实际耗时取决于本机资源和 Doris 负载。连接已有 Doris 时跳过本步骤，并在 `conf/app_config.yaml` 中填写对应连接信息。

### 4. 创建管理员

```bash
uv run -m scripts.bootstrap_admin
```

该命令读取 `conf/.env` 中的 `ADMIN_USERNAME`、`ADMIN_EMAIL` 和 `ADMIN_PASSWORD`，可重复执行。

### 5. 启动应用

在四个项目根目录终端中分别启动后端、前端、Celery Worker 和 Celery Beat：

```bash
# 终端 1：后端
uv run main.py

# 终端 2：前端
npm --prefix web run dev

# 终端 3：Celery Worker
uv run celery --app app.shared.tasks.celery_app:celery_app worker -l INFO

# 终端 4：Celery Beat
uv run celery --app app.shared.tasks.celery_app:celery_app beat -l INFO
```

启动后访问：

- 前端：<http://localhost:7001>
- 后端 OpenAPI：<http://localhost:7000/docs>

修改 `docker/sandbox` 中的依赖或 Dockerfile 后，重新构建沙箱镜像：

```bash
docker compose -f docker/compose.yml build sandbox-image
```

## 启动后页面配置

使用 `conf/.env` 中配置的管理员账号登录前端，点击左下角的“后台”按钮进入“管理中心”。

### 1. 元数据导入与索引同步

1. 打开“元数据管理”，在“元数据 YAML 导入导出”区域选择 `conf/meta_config.yaml`。
2. 模式选择“全量替换”，点击“执行导入”。
3. 导入完成后，系统会自动提交字段和指标的语义索引同步任务。保持 Celery Worker 运行，等待任务完成后刷新页面，确认对应索引状态为“已同步”。
4. 在“表元数据”区域全选数据表，点击“全量同步取值索引”，完成启用取值索引字段的首次同步。

### 2. 数据库角色创建与权限分配

1. 打开“Doris 角色管理”，点击“添加角色”，填写角色标识、查询用户、业务描述和资源工作组后创建角色。
2. 选中创建的角色，在“表与列数据权限 (SELECT)”区域配置查询权限：表名留空表示授予当前数据库全部表权限；填写表名并将字段留空表示授予整表权限；同时填写表名和逗号分隔的字段表示仅授予指定字段权限。
3. 按需配置行级策略，并可将该角色设为新用户的默认角色。
4. 打开“用户账号管理”，添加或编辑用户，将 Doris 角色分配给需要查询数据的账号。
