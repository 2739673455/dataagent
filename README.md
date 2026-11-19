# DataAgent

## 推荐运行环境

在本机运行整套服务（应用、PostgreSQL、Elasticsearch、Redis 和独立的 PostgreSQL 业务库），供单人使用并同时运行一个分析沙箱时，**建议至少配备 24 GB 内存**。该建议为容量估算，实际占用取决于数据规模、查询复杂度和并发量。

推荐使用 **Ubuntu Linux**；Windows 开发环境建议通过 **WSL2 + Ubuntu** 运行项目。

## 提示词与技能

角色提示词位于 `resources/prompts/agents/`，公共文件协议和标题提示词位于 `resources/prompts/`，内置技能位于 `resources/skills/`。部署时将 `resources/` 与 `app/` 一同复制。

## 配置

### 后端环境变量

首次部署时从模板创建仅本机可读的配置，已有配置不要覆盖：

```bash
umask 077
test -e conf/.env || cp conf/.env.example conf/.env
```

在 `conf/.env` 或部署平台的密钥管理器中填写以下值，不要提交或分享真实配置：

- `POSTGRES_PASSWORD`：内部 PostgreSQL 账号密码。新数据库使用密码管理器生成；已存在的数据卷使用现有密码，改环境变量不会轮换数据库密码。
- `BUSINESS_PG_ADMIN_PASSWORD`：业务库管理账号密码。已有库请填已有凭据；新建本地业务库请单独生成，不与内部库或管理员复用。
- `ADMIN_USERNAME`、`ADMIN_EMAIL` 和 `ADMIN_PASSWORD`：自行确定首次引导的平台管理员信息，密码单独生成。缺少密码时引导命令会停止。
- 按模板注释分别生成查询凭据加密密钥 `BUSINESS_PG_CREDENTIAL_ENCRYPTION_KEY` 和 JWT 签名密钥 `JWT_SECRET`，并配置所用外部服务的密钥。

数据库密码没有默认值。运行 Compose 时请指定 `--env-file conf/.env`；任一数据库密码为空或未设置都会在连接前报错。为新库或初始账号生成密码可在本地密码管理器操作，也可为每项独立运行 `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` 并妥善保存结果。

### 模型配置

在 [conf/app_config.yaml](conf/app_config.yaml) 中配置语言模型和向量模型，API 密钥通过 `${oc.env:变量名}` 从 `conf/.env` 或进程环境变量读取。修改后重启相关后端和 Worker 进程。

**语言模型（`lm_config`）**

- `models`：按配置名声明模型，`model_provider` 支持 `openai` 和 `deepseek`，其余字段为 `api_protocol`、`model`、`base_url` 和 `api_key`；`params` 用于传入推理强度等附加参数。
- `active`：选择 `models` 中的一个配置名，作为 Planner 的默认模型。
- `profile`：按模型实际能力填写图片输入支持（`image_inputs`）、结构化输出支持（`structured_output`）和上下文长度（`max_input_tokens`）。
- `agent.specialists`：可为 Explorer、Analyst、Reviewer 单独指定模型配置名；填写 `default` 时跟随 `lm_config.active`。

**向量模型（`embedding`）与 ES 维度**

`embedding` 的 `base_url`、`api_key` 和 `model` 指定文本向量化服务，用于元数据和查询经验的语义检索。

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
docker compose --env-file conf/.env -f docker/compose.yml up -d
```

该命令启动应用内部 PostgreSQL、Elasticsearch 和 Redis，并在缺少 `dataagent-sandbox:latest` 时构建沙箱镜像。PostgreSQL 的 `auth`、`meta` 和 `langgraph` 数据库会在首次创建数据卷时自动初始化。

如在已有 PostgreSQL 实例上补建内部库，可运行 `make init-db`。它只创建缺失的 `auth`、`meta`、`langgraph`；重复运行会保留现有库、表、权限及数据，不会重置数据库或触碰业务库。

本地需要从头开始时，备份后停止后端、Worker、Beat 并关闭数据库客户端，在主机终端运行 `make reset-db`。它调用**同一个** `scripts/init_db.py --reset`，显示端口、物理集群标识以及将被清空的 `auth`、`meta`、`langgraph`；只有完整输入 `RESET` 才继续。无需进入容器。默认 `make init-db` 始终只补建缺失库，不会询问或执行重置。

重置模式仅支持使用本仓库 `docker/compose.yml` 启动的专用本机 Linux/WSL 开发实例，要求可用本机 Docker CLI 和 Unix socket；会交叉核对容器、环回端口、开发标记、物理集群标识及数据库清单。如果数据库已与业务库或其他库共用、连接未关闭、存在预备事务或逻辑复制、不是交互终端、未准确确认，均拒绝操作，不会强制断开连接。旧容器需用新的 Compose 配置重建容器以加载开发标记，保留原数据卷。不要用删卷代替重建容器。

重置不连接业务库，也不会重建业务数据。三个内部库会像初次初始化一样成为空库；表分别由后台启动和管理员引导创建。请重新运行管理员引导、启动应用并重新配置角色与元数据。重置仅针对内部库：外部业务库中的原生角色/策略、Redis、Elasticsearch、沙箱文件都不会清理。PostgreSQL 不支持跨库原子重建；意外中断后应核对备份、三个库和连接许可的状态；只补建的初始化命令不会恢复已清空的数据，也不会改动现有库的连接许可。

查看服务状态：

```bash
docker compose -f docker/compose.yml ps
```

### 3. 接入业务 PostgreSQL

应用自身的 `auth`、`meta`、`langgraph` 库仍在原来的 PostgreSQL 实例。本步骤只配置装有业务表的数据库。

- **使用已有业务库**：在 `conf/.env` 中填写 `BUSINESS_PG_HOST`、`BUSINESS_PG_PORT`、`BUSINESS_PG_DATABASE`（默认 `ecommerce`）、`BUSINESS_PG_SCHEMA`（默认 `public`）及管理凭据。若本机已有实例占用 5432，请在同一配置文件设置 `APP_PG_PORT=5433`，以便内部库和业务库共存。
- **使用空的本地开发库**：运行 `docker compose --env-file conf/.env -f docker/compose.yml --profile local-business up -d`。业务库默认监听本机 5434（容器内 5432），应用内部库监听 5432。自行导入业务表；DataAgent 启动、导入元数据、授权和查询都不依赖 dbmock。若使用其他程序（例如 dbmock）写数据，指向同一业务实例、数据库与 schema 即可，不共享查询登录凭据。
- 业务管理账号需有 CREATEROLE、业务 schema 的 USAGE 和表的授权/策略管理权（通常由业务表 owner 或获准使用其所有者角色的账号执行）。建议使用独立业务实例、独立管理账号；应用账号不应持有集群级超级用户权限。Compose 创建的集群初始超级用户**仅用于本机演示**，生产环境应单独配置管理角色。
- 查询账号由“角色管理”创建，不是业务表 owner；不要另授超级用户、BYPASSRLS、写权限或非预期继承权限。请使用专用业务 schema，不要向 PUBLIC 授予 CREATE 或业务表 SELECT。在新库上建立授权；开发版不自动转换旧 Doris 用户、密码、策略及索引。已有部署请先备份并保留旧数据库；开发版采用新的身份表和字段，不能直接复用旧版内部库。使用独立的新内部库验证，或由 DBA 离线迁移并复核后切换。管理员需重建角色、分配用户、导入元数据并重新索引，不要将旧权限记录当作已生效。业务源表不会在应用启动时自动创建、清空或迁移。
- 如需匹配自己已有的表，请用实际元数据配置，不要直接导入示例文件。

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

1. 打开“PostgreSQL 角色管理”，点击“添加角色”，填写角色标识、查询用户、业务描述和资源配置（standard 或 reporting）后创建角色。
2. 选中创建的角色，在“表与列数据权限 (SELECT)”区域配置查询权限：表名留空表示授予指定 schema 现有业务表权限；新表需再次授权；填写表名并将字段留空表示授予整表权限；同时填写表名和逗号分隔的字段表示仅授予指定字段权限。
3. 按需配置行级策略，并可将该角色设为新用户的默认角色。
4. 打开“用户账号管理”，添加或编辑用户，将 PostgreSQL 角色分配给需要查询数据的账号。

## 验证 PostgreSQL 迁移

准备好 `conf/.env` 与 `uv sync` 后，安装 PostgreSQL 17 的服务端和客户端工具，用普通用户在项目根目录运行：

```bash
scripts/development/test_business_pg.sh -q
```

脚本使用随机端口、一次性密码和新临时目录启动本地 PostgreSQL，完成后关闭并清理；不连接或改动配置里的业务库。没有服务端工具时仍可使用 `uv run pytest` 跑离线测试，依赖真实数据库的用例将跳过。

管理员可用 `DATAAGENT_META_SOURCE_DSN` 环境变量指定业务库，执行 `uv run python -m scripts.development.generate_meta_config --schema public --check` 校验内置电商语义样例。该生成器基于 PostgreSQL 目录，查询不需要运行它。切换业务库或 schema 时应重建对应的元数据、索引、角色和查询经验，避免沿用其他来源的缓存。
