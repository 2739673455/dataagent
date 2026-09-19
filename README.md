# DataAgent

## 配置

### 后端环境变量

复制环境变量模板：

```bash
cp conf/.env.example conf/.env
```

编辑 `conf/.env`：

```dotenv
# ==================== 数据库 ====================

# PostgreSQL 数据库密码（认证、元数据和会话持久化共用）
POSTGRES_PASSWORD=123123

# Doris 平台内部管理账号密码，用于元数据读取和权限管理
DORIS_ADMIN_PASSWORD=123123

# Doris 查询身份凭据加密密钥
# python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
DORIS_CREDENTIAL_ENCRYPTION_KEY=

# ==================== 身份认证与管理员 ====================

# JWT 签名密钥，必须使用至少 32 字符的随机值
# python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
JWT_SECRET=

# 初始管理员引导凭据（scripts/bootstrap_admin.py）
# 用户名和邮箱可通过 CLI 覆盖；密码从环境变量读取
ADMIN_USERNAME=123
ADMIN_EMAIL=123@123.com
ADMIN_PASSWORD=123123

# ==================== 模型服务 ====================

# DeepSeek 官方 API 密钥
DEEPSEEK_API_KEY=

# OpenRouter 模型服务密钥
OPENROUTER_API_KEY=

# SiliconFlow 模型服务密钥
SILICONFLOW_API_KEY=

# ==================== MCP 工具 ====================

# Tavily MCP 搜索服务密钥
TAVILY_API_KEY=
```

按注释中的命令生成 Doris 凭据加密密钥和 JWT 签名密钥。

### 应用配置

[conf/app_config.yaml](conf/app_config.yaml) 决定应用连接哪些服务、使用哪些模型，以及查询、分析和后台任务可以占用多少资源。密码和 API 密钥通过 `${oc.env:变量名}` 从 `conf/.env` 或进程环境变量中读取，其余参数直接在 YAML 中设置。应用在启动时读取并校验配置，修改后需要重启相关后端、Worker 或 Beat 进程。

#### 服务连接与数据存储

本地使用项目提供的 Compose 时，配置中的服务地址和端口与其对应。连接已有服务时，按下表修改相应配置组。

| 配置组 | 用途与配置要点 |
| --- | --- |
| `port`、`cors_origins` | 后端默认监听 `7000` 端口。浏览器直接跨域访问后端时，在 `cors_origins` 中填写前端 Origin；通过前端开发代理访问时，配置下一节的代理地址。 |
| `log` | 设置日志级别和单个日志文件的滚动大小，默认 `INFO`、`10MB`。 |
| `doris` | 配置业务数据库和后台管理连接，用于读取元数据、管理角色和权限。用户执行 SQL 时使用平台角色对应的 Doris 查询账号。 |
| `auth_postgresql` | 保存平台账号、令牌、Doris 查询身份、权限指纹和用户注销任务。 |
| `meta_postgresql` | 保存元数据目录、查询经验、执行记录和召回快照。 |
| `langgraph_postgresql` | 保存会话目录、删除标记和 Agent 的 Checkpoint。三组 PostgreSQL 配置默认连接同一服务中的 `auth`、`meta`、`langgraph` 三个数据库。 |
| `elasticsearch` | 设置搜索服务地址、字段/指标/取值/查询经验的索引名称，以及向量维度 `embedding_size`。 |

Redis 分别用于后台任务、沙箱协调和认证限流。默认使用同一 Redis 服务的不同逻辑库：`task_queue.broker_url` 使用 `/0`，`task_queue.result_backend` 使用 `/1`，`sandbox.ownership.redis_url` 使用 `/2`，`auth.rate_limit_redis_url` 使用 `/3`。更换 Redis 服务时，需要同步核对这四处地址。

#### 语言模型、向量模型与外部工具

`lm_config.models` 按名称保存语言模型配置，`lm_config.active` 引用其中一个名称作为默认模型。每个模型需要填写供应商 `model_provider`、接口协议 `api_protocol`、供应商侧模型名 `model`、服务地址和 API 密钥。`params` 用于传入模型客户端的附加参数，例如推理强度。

模型的 `profile` 描述实际能力：`image_inputs` 决定是否启用图片输入能力，`structured_output` 影响专家结构化结果的生成方式，`max_input_tokens` 用于计算上下文压缩阈值。这些字段应与所用模型及接口的能力一致。

Planner 使用默认模型；Explorer、Analyst、Reviewer 可在 `agent.specialists` 中分别指定模型配置名，填写 `default` 时跟随 `lm_config.active`。例如，可以让 Explorer 使用一个模型负责取数，让 Analyst 使用另一个模型负责分析。

`embedding` 单独配置文本向量化服务，用于元数据和查询经验的语义搜索。它的模型输出维度必须与 `elasticsearch.embedding_size` 一致。`mcp` 配置加载给 Explorer 的外部工具服务，包含传输方式、服务地址及凭据引用；不使用的服务可以从该组中删除。

#### 查询与 Agent 执行

`query` 控制只读 SQL 的执行和结果摘要：默认单条查询超时为 300 秒、Doris 内存上限为 1 GiB，每批读取 100 行，返回给模型的摘要保留 5 行样例。`batch_size` 和 `sample_rows` 分别控制读取批次和预览大小，完整结果仍写入 CSV。`query_experience_vector_score_threshold` 控制查询经验向量召回的最低相似度。

`agent.orchestration` 控制每个会话的专家协作规模，默认最多同时执行 8 个专家任务、保留 128 个专家 Session，并允许 Planner 在需要自动续写时最多继续 3 次。`agent.interpreter.memory_limit_bytes` 设置 Planner 调度用 QuickJS 解释器的内存上限，默认 64 MiB。

#### 沙箱与文件资源

`sandbox` 配置用户分析代码运行的 Docker 容器及持久化数据卷。`image` 应与构建的沙箱镜像名一致；同一套部署的 API 和 Worker 共用 `deployment_namespace`，同一 Docker 主机上的不同部署使用不同值。

默认每个用户容器断网，内存限制为 2 GiB、CPU 配额为 2 核，整个部署最多同时运行 8 个用户容器。`max_file_bytes` 默认限制单文件 API 操作为 50 MiB；用户卷容量目标由 `max_user_storage_bytes` 设置，默认 `local` 卷驱动不提供总容量硬配额。`ownership` 中的 Redis 锁和租约协调多个进程对同一沙箱的操作。

空闲容器默认在 600 秒后停止、3600 秒后删除容器并保留数据卷，检查间隔为 60 秒。可按机器容量调整运行数量、资源限制和空闲回收时间。

#### 认证、后台任务与清理

`auth` 设置 JWT 签名、签发者、令牌有效期和密码要求，默认 Access Token 有效 15 分钟、Refresh Token 有效 30 天。`doris_credentials.encryption_key` 用于加密保存 Doris 查询账号密码，部署后应妥善保留；更换密钥会影响已有查询凭据的解密。

`task_queue` 设置 Celery 的消息队列、结果保存时间、任务时限和定时调度。默认结果保存一天，单任务软时限为 3300 秒、硬时限为 3600 秒；字段取值索引每天北京时间 08:00 调度，查询经验索引修复和会话清理每 300 秒调度。`metadata_index.value_lookback_seconds` 设置取值增量同步的回看窗口，默认 300 秒，用于补读近期数据。

`lifecycle` 设置草稿保留时间、清理批量大小及用户注销的补偿策略。草稿默认保留 24 小时；用户注销受理后立即投递任务，每 300 秒扫描漏投或待重试任务，失败后至少等待 60 秒才可再次领取。调整扫描周期影响补偿处理的及时性，正常首次注销由立即投递触发。

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

## 模块文档

项目介绍位于 [docs](docs/) 目录。建议先读总览，再沿“提问 → 找数据 → 执行查询 → 生成报告”阅读相关模块；接口索引集中在最后一篇。

- [00. 架构与协作总览](docs/00_架构与协作总览.md)：系统总体架构、数据流向与多 Agent 协作机制。
- [01. Shared 基础能力](docs/01_公共基础能力.md)：数据库与外部服务连接、公共数据格式、统一错误响应、请求跟踪与后台任务。
- [02. Identity 认证与授权](docs/02_认证与授权.md)：用户认证、Token 管理、Doris 账号与查询权限控制（列级与行级策略）。
- [03. Metadata 元数据与语义召回](docs/03_元数据与语义召回.md)：表/字段/指标元数据管理、Elasticsearch 语义与取值索引。
- [04. Sandbox 隔离工作区](docs/04_沙箱与隔离工作区.md)：基于 Docker 容器的隔离执行环境、文件权限与多进程沙箱协调。
- [05. Query 安全查询链路](docs/05_安全查询与经验沉淀.md)：只读 SQL 校验与安全执行、结果导出为 CSV 及查询经验沉淀。
- [06. Assistant 多 Agent 分析体系](docs/06_对话与多智能体协作.md)：Planner、Explorer、Analyst、Reviewer 的调度执行、会话管理与流式推送。
- [07. Workflows 跨存储工作流](docs/07_跨模块工作流.md)：用户注销的立即投递、补偿清理，以及元数据变更后的跨模块协作。
- [08. 接口索引](docs/08_接口索引.md)：全部业务 HTTP 操作及其代码入口。
