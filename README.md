# DataAgent

## 推荐运行环境

在本机运行整套服务（应用、PostgreSQL、Elasticsearch、Redis 和 Doris），供单人使用并同时运行一个分析沙箱时，**建议至少配备 24 GB 内存**。该建议为容量估算，实际占用取决于数据规模、查询复杂度和并发量。

推荐使用 **Ubuntu Linux**；Windows 开发环境建议通过 **WSL2 + Ubuntu** 运行项目。

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

### 模型配置

在 [conf/app_config.yaml](conf/app_config.yaml) 中配置语言模型和向量模型，API 密钥通过 `${oc.env:变量名}` 从 `conf/.env` 或进程环境变量读取。修改后重启相关后端和 Worker 进程。

**语言模型（`lm_config`）**

- `models`：按配置名声明模型，填写 `model_provider`、`api_protocol`、`model`、`base_url` 和 `api_key`；`params` 用于传入推理强度等附加参数。仅 DeepSeek Responses 使用专用适配，其余供应商统一使用 OpenAI 兼容接口；供应商扩展请求字段放在 `params.extra_body` 中。
- `active`：选择 `models` 中的一个配置名，作为 Planner 的默认模型。
- `profile`：按模型实际能力填写图片输入支持（`image_inputs`）、结构化输出支持（`structured_output`）和上下文长度（`max_input_tokens`）。
- `agent.specialists`：可为 Explorer、Analyst、Reviewer 单独指定模型配置名；填写 `default` 时跟随 `lm_config.active`。

**向量模型（`embedding`）与 ES 维度**

`embedding` 的 `base_url`、`api_key` 和 `model` 指定文本向量化服务，用于元数据的语义检索。

`elasticsearch.embedding_size` 必须等于向量模型实际输出的维度；它只定义 ES 索引的向量维度，不会改变模型输出。更换向量模型后需重新生成索引中的向量；如果维度变化，还需按新维度重建相关 ES 索引。

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

默认应用连接 Doris 的 `ecommerce` 数据库。`dbmock` 作为 Git 子模块固定到指定提交，其数据文件由子仓库的 Git LFS 管理。先在项目根目录初始化子模块并拉取数据：

```bash
git lfs install
git submodule update --init
git -C dbmock lfs pull
```

首次克隆主仓库时可添加 `--recurse-submodules` 参数。之后更新主仓库代码时，执行 `git submodule update --init` 同步子模块版本。

创建 `dbmock` 配置并按指定月份生成数据：

```bash
cp dbmock/.env.example dbmock/.env
# 将 dbmock/.env 中的 DB_PASSWORD 设置为 123123

cd dbmock
uv sync
uv run scripts/init_db.py
uv run main.py --start-month 2026-01 --end-month 2026-08
cd ..
```

`dbmock/scripts/init_db.py` 会删除并重建 `DB_NAME` 指定的数据库，只能用于可重建的本地数据。生成耗时取决于月份范围、数据规模、本机资源和 Doris 负载。

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

### 1. 元数据导入

在项目根目录执行全量导入，默认读取 `conf/meta_config.yaml`：

```bash
uv run -m scripts.import_metadata --full
# 指定其他 YAML 文件
uv run -m scripts.import_metadata --full --config /path/to/metadata.yaml
```

脚本先校验 YAML 结构、名称和引用及 Doris 源表，然后删除全部元数据目录、取值同步状态、召回快照和三个元数据索引，重新写入目录并完成字段、指标和字段取值索引。修改 YAML 或更换向量模型后重新执行全量导入。

后续仅追加字段取值：

```bash
uv run -m scripts.import_metadata --incremental
```

增量脚本使用已导入目录中的 `value_index_cursor_column`，每张表读取一次最大水位。水位未推进则跳过；有新数据时读取 `(上次水位, 本次最大水位]` 中启用 `index_values` 的字段取值，索引写入成功后提交新水位。未配置水位的表跳过，全量时为空的表可在后续有数据时开始增量导入。

水位字段需要在新增或更新时递增；相同或更旧水位的迟到数据、源数据删除不会由增量脚本修复，应重新全量导入。增量失败可直接重跑，已完成字段保留水位，失败字段从旧水位重试；全量失败重新执行全量脚本。两种模式互斥运行，失败返回非零退出码，不依赖 API、Celery Worker 或 Beat。

### 2. 数据库角色创建与权限分配

1. 打开“Doris 角色管理”，点击“添加角色”，填写角色标识、查询用户、业务描述和资源工作组后创建角色。
2. 选中创建的角色，在“表与列数据权限 (SELECT)”区域配置查询权限：表名留空表示授予当前数据库全部表权限；填写表名并将字段留空表示授予整表权限；同时填写表名和逗号分隔的字段表示仅授予指定字段权限。
3. 按需配置行级策略，并可将该角色设为新用户的默认角色。
4. 打开“用户账号管理”，添加或编辑用户，将 Doris 角色分配给需要查询数据的账号。
