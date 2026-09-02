# DataAgent

基于多 Agent 协作、Docker 容器隔离沙箱与语义知识检索的智能数据分析平台。

后端采用模块化单体结构，代码划分为 `identity`、`metadata`、`query`、`assistant`、`sandbox`、`workflows`、`shared` 七个一级模块。完整功能树和依赖约束见[架构总览](docs/00_ARCHITECTURE_OVERVIEW.md)。

---

## 已实现功能需求

### 1. 多用户 Docker 隔离沙箱系统
- **容器与工作区隔离**：基于用户维度动态拉起独立的 Docker 容器，并基于会话（Conversation）创建隔离的沙箱文件目录，保障分析过程与产物的环境安全。
- **沙箱文件系统操作**：提供完整的沙箱文件管理接口，支持文件与目录的创建、读取、写入、列出、搜索、删除以及文件大小/类型检测。
- **命令受控执行**：支持在用户沙箱容器内异步执行命令，提供超时控制、标准输入输出捕获与退出码检测。
- **生命周期自动化管理**：支持容器按需拉起、空闲超时自动清理、会话目录即时回收与用户级资源注销。
- **分析运行时环境**：沙箱镜像内置 Python 3.12、Node.js 等基础环境，支持代码执行、数据分析与图表生成。

### 2. 元数据管理与资产同步
- **数据源元数据同步**：支持从 Doris 等分析型数据库中抽取表结构、字段类型、注释及业务描述。
- **统一元数据目录**：基于 PostgreSQL 统一管理表（`TableInfo`）、字段（`ColumnInfo`）、指标（`MetricInfo`）及跨表主外键关联关系（`SemanticRelation`）。
- **枚举值索引管理**：支持字段枚举值的采样与状态跟踪，具备同步中、成功、失败等状态流转机制。
- **元数据 REST API**：提供表、字段、指标与枚举值的全套查询、同步与维护接口。

### 3. 双路语义索引与混合检索（Hybrid Search）
- **混合索引存储**：基于 Elasticsearch 分别构建字段、指标和枚举值的全文索引（IK 中文分词 + 精确匹配加权）与向量索引（HNSW 稠密向量）。
- **文本向量化计算**：集成 Embedding 服务，支持批量计算检索词的高维语义向量。
- **RRF 倒数排名融合**：基于 RRF（Reciprocal Rank Fusion）算法融合多路全文与向量召回结果，实现类型内归一化打分与确定性排序。
- **结构化命中溯源**：提供结构化的命中原因（`SemanticMatchReason`），记录匹配类型、检索词和原始分数，消除文本拼接歧义。
- **检索并发与过载保护**：通过异步信号量（`asyncio.Semaphore`）限制并发检索量，支持元数据并发并行加载。
- **关联上下文智能展开**：检索结果自动关联指标依赖字段、枚举所属字段，并智能补充相关表的一层主外键关系与主键信息，具备防截断保护机制。

### 4. 会话级语义召回管理（Semantic Recall）
- **召回快照持久化**：每次语义检索请求与结果自动保存为会话级召回快照（`SemanticRecallRecord`），`created_at` 记录 query 上下文的首次创建时间，`updated_at` 记录当前累计快照的更新时间；两者随召回内容一同提供给模型。
- **召回记录生命周期管理**：
  - **列表查看（`list_recalls`）**：按会话分页查看历史召回摘要及各类型候选数量。
  - **详情获取（`get_recall`）**：获取单条召回快照的完整参数、命中结果与关联上下文。
  - **多轮合并（`merge_recalls`）**：支持将多轮检索产物去重合并并保存为新快照，便于多步骤分析沉淀上下文。
  - **记录清理（`delete_recalls`）**：支持批量删除无用的召回记录。
- **Agent 工具集成**：将语义检索与召回管理封装为标准 LangChain 工具，供大模型在规划阶段调用。

### 5. 智能体运行时与会话对话系统
- **动态 Agent 编排**：基于 `deepagents` 与 LangGraph 构建持久化 Planner 和专业 Agent，Planner 通过 `delegation` 调度注册的专业 Agent，每类 Agent 使用独立工具白名单。
- **会话状态持久化**：使用 LangGraph PostgreSQL Checkpointer 保存对话消息、中间推理过程与图状态；会话目录和删除墓碑使用同库正式关系表管理。
- **流式对话接口（SSE）**：提供 Server-Sent Events 流式对话响应，支持模型 Token 增量输出与工具调用过程实时推送。
- **多模态与消息处理**：根据模型能力投影图片等附件；图片工具结果和语义召回在 checkpoint 中只保留持久化引用，完整内容在模型请求中临时展开。
- **会话全生命周期管理**：支持会话创建、历史消息回溯、会话重命名、软删除及级联清理。

### 6. 附件与文件服务
- **工作区附件上传与下载**：提供会话专属的附件上传通道，进行工作区路径越界检查与安全性规范化。
- **文件预览与访问**：支持沙箱内生成的文件、图片等产物的流式下载与预览接口。

### 7. 工程架构与质量保障
- **异步高性能架构**：全面采用异步 I/O，集成 PostgreSQL（`asyncpg`/`psycopg` 连接池）、Doris（`asyncmy`）及 Elasticsearch 异步客户端。
- **配置与日志体系**：采用 YAML + OmegaConf + Pydantic 分层配置，结合 Loguru 结构化日志与全链路 TraceId 中间件。
- **代码质量与测试**：核心应用通过 Pyright 严格类型检查与 Ruff 代码规范校验，配备单元测试与沙箱集成测试套件。

### 8. 用户认证、Doris RBAC 与多租户授权
- **认证与令牌安全**：支持用户名或邮箱登录、登出和当前用户查询；密码使用 Argon2id 哈希，JWT Access Token 与 Refresh Token 支持轮换、重放检测和令牌族吊销，登录与刷新接口带有过载保护。
- **单一 Doris 数据角色**：每个普通用户绑定一个 `doris_role_name`，多个平台用户可以共享同一 Doris 查询用户；每个查询用户只绑定一个同名权限角色。管理员创建用户时自动使用数据库中的唯一缺省角色。
- **Doris 细粒度权限**：表级和列级 `SELECT_PRIV`、角色 Row Policy 由 Doris 执行；成功的 SELECT 授权同步到应用侧可见性投影，语义检索、召回快照和 SQL Guard 在连接 Doris 前按当前角色过滤。
- **管理员边界**：只有平台管理员可以查看或修改元数据、用户角色绑定和 Doris 角色权限；最后一位平台管理员受防护。
- **租户隔离**：会话、附件、语义召回、LangGraph 线程、Agent Session 和 Docker 工作区均绑定 `user_id` 与 `conversation_id`，越权访问在路由或服务层拦截。

### 9. SQL 只读查询工具（`execute_sql`）
- **单一 SQL Tool**：数据查询 Agent 只暴露 `execute_sql`，不提供可绕过执行链的独立 SQL 校验 Tool。
- **执行前完整校验**：工具首先使用 `sqlglot` 按 Doris / MySQL 方言检查语法、单条只读约束、资产权限、表、字段、别名、CTE、类型和 JOIN；失败时不连接 Doris，直接返回 `sql_validation_failed`、问题列表和修正提示。
- **稳定查询身份**：PostgreSQL 动态保存 Doris 角色、共享查询用户、加密密码和 Workload Group；服务端按 `users.doris_role_name` 解密凭据并按需创建独立连接池，客户端不能指定查询身份。
- **数据库侧权限校验**：应用启动时逐一通过 `SHOW GRANTS` 检查查询账号只绑定预期角色、仅具备只读权限、可见目标数据库并可使用指定 Workload Group。
- **查询前资源守卫**：在读取数据前执行 `EXPLAIN`，要求 ScanNode 提供有效的扫描行数和扫描字节估算并记录到执行审计；查询会话同时设置 workload group、超时和内存限制。
- **受控流式输出**：服务端游标分批读取并防护 CSV 公式注入；查询受 Doris 超时和内存限制，超时或取消时作废当前连接，产物保存仍受沙箱文件和工作区容量限制。
- **会话产物**：CSV 写入 `/sessions/{analysis_id}/{agent_type}/{session_id}/query_{uuid}.csv`，Agent 仅接收路径、字段信息、行数、时间范围和少量样例。

### 10. Dynamic Subagents 与多 Agent 体系
- **Planner 协调智能体**：Planner 通过结构化 `delegation` 请求拆分任务、并行调度专业 Agent 并汇总可追溯结果；自动续写次数与并行 Session 数受服务端硬限制，连续重复的修补请求会被服务端终止。
- **结构化修补链路**：专业 Agent 可返回带产物证据的 `RepairRequest`，仅能指向同一 Analysis 内已存在的上游 Session；服务端检查目标和实际产物后续接执行。
- **专业 Agent 矩阵**：`explorer` 负责语义目录、MCP 外部能力和受控 SQL 数据集；`analyst` 自主完成统计归因、图表和自包含 HTML 报告；`reviewer` 独立审查数据、分析结论与产物并发起修补。
- **按 Agent 聚合代码**：`app/assistant/agents` 包含 Planner 和三个专业 Agent，每个 Agent 目录聚合自己的构造器与 Prompt；跨 Agent 协议、注册表和 Session 管理位于公共层，平台级数据查询工具归属于 `explorer` Agent。
- **专业 Agent 通用执行能力**：分析和审查 Agent 使用 DeepAgents 内置的 Shell 与文件工具，在各自 Session 沙箱中编写、运行、修改和验证代码。算法与核验方法由 Agent 根据数据和业务问题自主选择，代码、参数和结果作为产物保留。
- **Planner 文件查看能力**：Planner 使用只读文件工具和 Conversation 级 Shell 查看用户附件、目录与已知产物，数据处理和文件修改继续交由专业 Agent 完成。
- **Session-aware 状态管理**：各 Session 使用 `subagents/{analysis_id}/{agent_type}/{session_id}` 作为 `checkpoint_ns`，状态保存在 PostgreSQL，支持并行分析、服务重启后续接和删除墓碑。
- **产物边界**：Session 产物限定在 `/sessions/{analysis_id}/{agent_type}/{session_id}/`；结构化结果返回前会校验路径和文件存在性，用户附件上传与删除不能改写该系统目录，会话整体删除仍会统一清理产物。

---

## 部署与安全配置

### 1. 后端环境变量

复制 `conf/.env.example` 为 `conf/.env`，至少配置数据库密码、模型密钥和以下安全变量：

- `JWT_SECRET`：至少 32 字符的高强度随机值，生产环境由密钥管理系统注入。
- `DORIS_ADMIN_PASSWORD`：平台内部 Doris 管理账号密码，用于元数据读取和管理员权限操作，不进入 Agent 查询路径。
- `DORIS_CREDENTIAL_ENCRYPTION_KEY`：加密 PostgreSQL 中 Doris 查询用户密码的 Fernet 密钥。丢失该密钥后无法恢复已有查询身份凭据。
- `ADMIN_USERNAME`、`ADMIN_EMAIL`、`ADMIN_PASSWORD`：管理员引导凭据。用户名和邮箱可由 CLI 覆盖；密码只从环境读取，避免进入进程参数和 Shell 历史。

### 2. Doris 角色、稳定查询身份与 Workload Group

- `doris` 配置使用平台内部管理账号。该账号读取元数据并执行 Doris 用户、角色、SELECT 权限和 Row Policy 管理；部署时限制来源地址并妥善托管 `DORIS_ADMIN_PASSWORD`。
- 管理员通过 `POST /api/v1/admin/doris-roles` 创建角色。服务端生成随机查询密码，在 Doris 创建角色与唯一查询用户，把 Workload Group 的 `USAGE_PRIV` 授予角色，并只将密文保存到 `doris_query_identities`。API 不接收或返回查询密码。
- 新角色创建后保持普通状态。管理员可通过 `PUT /api/v1/admin/doris-roles/{role}/default` 设置新用户默认角色，通过 `DELETE /api/v1/admin/doris-roles/default` 恢复为“未分配”。默认角色可以删除，仍被用户引用的角色不能删除。
- 查询用户不授予导入、建表、修改、删除、授权或节点管理权限。应用启动时逐个检查已登记身份只绑定预期角色、有效权限只读、目标库可见和 Workload Group 可用；检查失败会记录警告并继续启动，实际查询仍在身份解析和 Doris 权限边界失败。
- 管理员 API 可直接操作 Doris：`GET|POST /api/v1/admin/doris-roles` 与 `DELETE /api/v1/admin/doris-roles/{role}` 管理查询身份，`GET|POST|DELETE /api/v1/admin/doris-roles/{role}/select-grants` 管理库、表、列 SELECT 权限，`GET|POST|DELETE /api/v1/admin/doris-roles/{role}/row-policies` 管理行策略。
- 平台管理员登录后可从聊天侧栏进入 `/admin`，在同一页面调整用户唯一 Doris 角色、平台管理员身份、SELECT 权限和 Row Policy。
- SELECT 授权必须通过管理员 API 修改，使 Doris 权限与应用侧语义检索投影同步。外部 DBA 修改后需要通过同一 API重放对应授权目标。

- 平台只管理自身创建的 Doris 角色和查询身份，已有 Doris 原生角色保持在平台管理边界之外。

### 3. 管理员引导

完成 `conf/.env` 和 PostgreSQL 配置后，显式执行幂等的管理员引导工具。工具会读取 `conf/.env` 和进程环境；用户名、邮箱可由命令行覆盖，密码只读取 `ADMIN_PASSWORD`：

```bash
# 使用 conf/.env 中的全部引导凭据
uv run python -m scripts.bootstrap_admin

# 通过环境变量提供密码，并覆盖用户名和邮箱
ADMIN_PASSWORD='replace-with-a-strong-password' \
uv run python -m scripts.bootstrap_admin -u admin -e admin@example.com
```

首次引导管理员可以暂时没有数据角色，平台管理员身份也不会映射成 Doris 管理角色。平台管理员登录 `/admin` 创建 Doris 角色后，将其分配给需要查询数据的用户；管理员可按需将角色设为缺省角色。平台管理员通过 `POST /api/v1/admin/users` 创建用户，通过 `PUT /api/v1/admin/users/{user_id}` 统一修改用户唯一 Doris 角色和平台管理员身份。元数据 REST 接口全部要求平台管理员身份。

### 4. 授权撤销与历史留存

Doris 角色或 SELECT 权限变更会立即作用于新的目录读取、语义检索、召回读取和 SQL 校验，Row Policy 由 Doris 自动追加到实际查询。已写入用户会话的历史消息与分析产物按会话留存策略保存，仍由原会话所有者读取；需要同时清除历史副本时，删除对应会话以级联清理 Checkpoint 和 Docker 工作区。

### 5. 启动、前端代理与 Docker 部署边界

```bash
docker compose -f docker/compose.yml up -d
uv sync --group dev
make run
make worker
make beat
```

- 完整执行 `docker compose -f docker/compose.yml up -d` 时，Compose 会在沙箱镜像缺失时自动构建 `dataagent-sandbox:latest`，已有镜像时直接复用。`sandbox-image` 使用零副本配置，不会创建固定沙箱容器；用户沙箱仍由应用按需创建。
- FastAPI 和 Celery Worker 启动时只校验 `sandbox.image` 对应的镜像，不执行镜像构建。修改沙箱依赖或 Dockerfile 后执行 `docker compose -f docker/compose.yml build sandbox-image` 主动更新镜像。
- 后端默认监听 `7000` 端口。Vite 开发代理的 `VITE_APP_PROXY` 默认为 `http://localhost:7000`，可复制 `web/.env.example` 并在非默认部署中覆盖。
- Redis 默认监听 `6379` 端口：DB 0 作为 Celery Broker，DB 1 保存 24 小时任务结果，DB 2 协调 Docker 沙箱跨进程所有权，DB 3 保存跨 API Worker 共享的认证限流状态。API 和 Worker 必须连接同一 Redis 实例。
- 使用 `make worker` 启动消费全部队列的 Celery Worker。详细说明见[Shared 共享基础设施功能](docs/07_SHARED.md)。
- `lifecycle` Worker 必须能够访问 Docker Engine，Celery Beat 只运行一个实例。
- 同一 Docker 主机上的不同部署必须使用唯一的 `sandbox.deployment_namespace`，避免容器和数据卷名称冲突。
- 同一 `deployment_namespace` 的 API 与 Celery Worker 必须共享 `sandbox.ownership.redis_url`。Redis 租约负责 UID 注册表、工作区维护、全局容器容量和关闭收尾协调，可以运行多个 Uvicorn/Gunicorn Worker。

---

## 前后端协议与文档检查

后端 FastAPI OpenAPI 是 HTTP 请求、响应和错误结构的协议源。`scripts/development/generate_openapi_types.py` 将组件 Schema、路由参数和响应生成到 `web/src/api/generated.ts`，前端业务类型直接引用该文件。生成文件不手工修改。

```bash
# 后端 Schema 变化后重新生成
uv run python scripts/development/generate_openapi_types.py

# 提交前检查协议和文档链接
uv run python scripts/development/generate_openapi_types.py --check
uv run python scripts/development/check_doc_links.py

# 也可从前端目录运行协议检查
cd web
npm run contract:check
```

CI 会独立运行协议差异检查、API 契约测试和文档本地链接检查。
