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

# PostgreSQL 数据库密码（身份、元数据和会话持久化共用）
POSTGRES_PASSWORD=123123

# Doris 平台内部管理账号密码，用于元数据读取和权限管理
DORIS_ADMIN_PASSWORD=123123

# Doris 查询身份凭据加密密钥
# python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
DORIS_CREDENTIAL_ENCRYPTION_KEY=

# ==================== 模型服务 ====================

# DeepSeek 官方 API 密钥
DEEPSEEK_API_KEY=

# SiliconFlow 模型服务密钥
SILICONFLOW_API_KEY=


```

按注释中的命令生成 Doris 凭据加密密钥。

### 模型配置

在 [conf/app_config.yaml](conf/app_config.yaml) 中配置语言模型和向量模型，API 密钥通过 `${oc.env:变量名}` 从 `conf/.env` 或进程环境变量读取。修改后重启后端进程。

**语言模型（`lm_config`）**

- `models`：按配置名声明模型，填写 `model_provider`、`model`、`base_url` 和 `api_key`；`params` 用于传入推理强度等附加参数。统一使用 Chat Completions；DeepSeek 使用 langchain-deepseek 并补充思考内容回传，其余供应商使用 OpenAI 兼容客户端；供应商扩展请求字段放在 `params.extra_body` 中。
- `active`：选择 `models` 中的一个配置名，作为 Planner 的默认模型。
- `profile`：按模型实际能力填写图片输入支持（`image_inputs`）、结构化输出支持（`structured_output`）和上下文长度（`max_input_tokens`）。
- `agent.specialists`：可为 Explorer、Analyst、Reviewer 单独指定模型配置名；填写 `default` 时跟随 `lm_config.active`。

**向量模型（`embedding`）与 ES 维度**

`embedding` 的 `base_url`、`api_key` 和 `model` 指定文本向量化服务，用于元数据的语义检索。

`elasticsearch.embedding_size` 必须等于向量模型实际输出的维度；它只定义 ES 索引的向量维度，不会改变模型输出。更换向量模型后需重新生成索引中的向量；如果维度变化，还需按新维度重建相关 ES 索引。

### 提示词与技能资源

静态提示词位于 `resources/assistant/<角色>/prompt.md`，标题提示词位于 `resources/assistant/title.md`；Analyst 技能位于 `resources/assistant/analyst/skills/`，以只读方式挂载到沙箱的 `/skills/analyst/`。

提示词在后端进程加载时读取，修改后重启后端。路径根据项目位置解析，不依赖启动目录。部署时须将 `resources/` 与 `app/` 一起交付，并保留相同目录关系；缺少提示词文件会导致启动失败。

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

### 4. 初始化预定义用户和角色

```bash
uv run -m scripts.bootstrap_users
```

预定义配置位于 `scripts/bootstrap_users.py`，当前包含用户 `admin` 和角色 `dataagent_admin`。脚本授予该角色 `cfg.doris.database` 全部表的查询权限，并创建专用查询账号。脚本可重复执行，查询密码加密保存在身份数据库中；不再需要平台登录密码或 JWT 配置。

开发数据库中已有旧版用户表时，需先按项目初始化流程重建表，再执行本脚本；脚本本身不会迁移或删除旧表。

### 5. 启动应用

标题生成和会话删除由后端的异步任务执行；过期草稿及待删除会话在启动时和每隔 300 秒清理一次，间隔与任务超时通过 `lifecycle` 配置。进程退出会取消后台任务；删除记录在下次扫描时继续处理，标题生成中断后保留即时标题。

在两个项目根目录终端中分别启动后端和前端：

```bash
# 终端 1：后端
uv run main.py

# 终端 2：前端
npm --prefix web run dev
```

启动后访问：

- 前端：<http://localhost:7001>
- 后端 OpenAPI：<http://localhost:7000/docs>

## 启动后使用

在前端选择 `admin` 开始分析，聊天页可切换用户。请求通过 `X-User-ID` 选择身份，不进行密码认证；会话和沙箱仍按用户 ID 隔离。

### 1. 元数据导入

在项目根目录执行全量导入，默认读取 `conf/meta_config.yaml`：

```bash
uv run -m scripts.import_metadata --full
# 指定其他 YAML 文件
uv run -m scripts.import_metadata --full --config /path/to/metadata.yaml
```

脚本先校验 YAML 结构、名称和引用及 Doris 源表，然后删除全部元数据目录、取值同步状态和三个元数据索引，重新写入目录并完成字段、指标和字段取值索引。修改 YAML 或更换向量模型后重新执行全量导入。

后续仅追加字段取值：

```bash
uv run -m scripts.import_metadata --incremental
```

增量脚本使用已导入目录中的 `value_index_cursor_column`，每张表读取一次最大水位。水位未推进则跳过；有新数据时读取 `(上次水位, 本次最大水位]` 中启用 `index_values` 的字段取值，索引写入成功后提交新水位。未配置水位的表跳过，全量时为空的表可在后续有数据时开始增量导入。

水位字段需要在新增或更新时递增；相同或更旧水位的迟到数据、源数据删除不会由增量脚本修复，应重新全量导入。增量失败可直接重跑，已完成字段保留水位，失败字段从旧水位重试；全量失败重新执行全量脚本。两种模式互斥运行，失败返回非零退出码，由脚本独立执行。

### 2. 预定义用户与数据权限

用户、角色及绑定在 `scripts/bootstrap_users.py` 中定义，执行 `uv run -m scripts.bootstrap_users` 初始化。当前 `admin` 拥有业务库全部表的只读查询权限；Doris 内置的全局 `admin` 角色不用于业务查询。应用不再提供用户和角色管理页面或写入接口。
