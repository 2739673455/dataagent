# DataAgent

基于多 Agent 协作、Docker 容器隔离沙盒与语义知识检索的智能数据分析平台。

---

## 已实现功能需求

### 1. 多用户 Docker 隔离沙盒系统
- **容器与工作区隔离**：基于用户维度动态拉起独立的 Docker 容器，并基于会话（Conversation）创建隔离的沙盒文件目录，保障分析过程与产物的环境安全。
- **沙盒文件系统操作**：提供完整的沙盒文件管理接口，支持文件与目录的创建、读取、写入、列出、搜索、删除以及文件大小/类型检测。
- **命令受控执行**：支持在用户沙盒容器内异步执行命令，提供超时控制、标准输入输出捕获与退出码检测。
- **生命周期自动化管理**：支持容器按需拉起、空闲超时自动清理、会话目录即时回收与用户级资源注销。
- **分析运行时环境**：沙盒镜像内置 Python 3.12、Node.js 等基础环境，支持代码执行、数据分析与图表生成。

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
- **召回快照持久化**：每次语义检索请求与结果自动保存为会话级召回快照（`SemanticRecallRecord`），并存储至 PostgreSQL。
- **召回记录生命周期管理**：
  - **列表查看（`list_semantic_recalls`）**：按会话分页查看历史召回摘要及各类型候选数量。
  - **详情获取（`get_semantic_recall`）**：获取单条召回快照的完整参数、命中结果与关联上下文。
  - **多轮合并（`merge_semantic_recalls`）**：支持将多轮检索产物去重合并并保存为新快照，便于多步骤分析沉淀上下文。
  - **记录清理（`delete_semantic_recalls`）**：支持批量删除无用的召回记录。
- **Agent 工具集成**：将语义检索与召回管理封装为标准 LangChain 工具，供大模型在规划阶段调用。

### 5. 智能体运行时与会话对话系统
- **动态 Agent 编排**：基于 `deepagents` 与 LangGraph 构建持久化 Planner 和专业 Agent，Planner 通过 `delegate_agent` 调度注册的专业 Agent，每类 Agent 使用独立工具白名单。
- **会话状态持久化**：集成 LangGraph PostgreSQL Checkpointer 与 Store，实现对话消息、中间推理过程与状态的完整持久化和续接。
- **流式对话接口（SSE）**：提供 Server-Sent Events 流式对话响应，支持模型 Token 增量输出与工具调用过程实时推送。
- **多模态与消息处理**：支持图片等附件的解析和传递，提供语义召回等大型消息的紧凑化压缩处理。
- **会话全生命周期管理**：支持会话创建、历史消息回溯、会话重命名、软删除及级联清理。

### 6. 附件与文件服务
- **工作区附件上传与下载**：提供会话专属的附件上传通道，进行工作区路径越界检查与安全性规范化。
- **文件预览与访问**：支持沙盒内生成的文件、图片等产物的流式下载与预览接口。

### 7. 工程架构与质量保障
- **异步高性能架构**：全面采用异步 I/O，集成 PostgreSQL（`asyncpg`/`psycopg` 连接池）、Doris（`asyncmy`）及 Elasticsearch 异步客户端。
- **配置与日志体系**：采用 YAML + OmegaConf + Pydantic 分层配置，结合 Loguru 结构化日志与全链路 TraceId 中间件。
- **代码质量与测试**：核心应用通过 Pyright 严格类型检查与 Ruff 代码规范校验，配备单元测试与沙盒集成测试套件。

### 8. 用户认证、RBAC 与多租户授权
- **认证与令牌安全**：支持注册、用户名或邮箱登录、登出和当前用户查询；密码使用 Argon2id 哈希，JWT Access Token 与 Refresh Token 支持轮换、重放检测和令牌族吊销，注册、登录与刷新接口带有过载保护。
- **平台角色管理**：内置 Admin、Analyst 和 Viewer 角色，公开注册用户固定获得 Viewer；Admin 可管理用户角色与资产白名单，最后一名 Admin 受防护。
- **数据资产白名单**：支持数据源、数据库、数据表和字段四级授权；元数据目录、语义检索、召回快照和 SQL Guard 都在返回或执行前按当前权限过滤。
- **租户隔离**：会话、附件、语义召回、LangGraph 线程、Agent Session 和 Docker 工作区均绑定 `user_id` 与 `conversation_id`，越权访问在路由或服务层拦截。

### 9. SQL 语法与安全检查工具（`check_sql_syntax`）
- **AST 静态检查**：使用 `sqlglot` 按 Doris / MySQL 方言解析 SQL，仅接受单条 `SELECT` / `WITH` 查询。
- **只读边界**：拒绝 DML、DDL、多语句、查询 Hint、参数占位符、高风险函数、表值函数和无约束 JOIN，并在执行前输出确定性校验结果。
- **目录与权限对齐**：先按资产白名单构造用户可见目录，再检查表、字段、别名、CTE、类型与 JOIN 条件，避免通过校验信息探测未授权资产。

### 10. SQL 只读查询工具（`run_readonly_sql`）
- **独立只读连接**：使用 `doris_query` 独立连接池和凭据，启动时通过 `SHOW GRANTS` 检查有效权限，并验证查询账号可进入指定 `workload_group`。
- **查询前资源守卫**：在读取数据前执行 `EXPLAIN`，校验扫描行数和扫描字节估算，估算缺失或超限时拒绝执行；查询会话同时设置 workload group、超时、内存和单元格限制。
- **有界流式输出**：服务端游标分批读取，强制最大行数与 UTF-8 输出字节数，并防护 CSV 公式注入；超时或取消时作废当前连接。
- **会话产物**：CSV 写入 `/analyses/{analysis_id}/sessions/{agent_type}/{session_id}/query_{uuid}.csv`，Agent 仅接收路径、Schema、行数、时间范围和少量样例。

### 11. Dynamic Subagents 与多 Agent 体系
- **Planner 协调智能体**：Planner 通过结构化 `delegate_agent` 请求拆分任务、并行调度专业 Agent 并汇总可追溯结果；同一用户回合的所有自动续写共享委派预算，自动续写次数、委派次数、并行 Session、修补轮次、修补深度和 Session 续接次数均有服务端硬限制。
- **结构化修补链路**：专业 Agent 可返回带产物证据的 `RepairRequest`，仅能指向同一 Analysis 内已存在的上游 Session；服务端检查目标、修补深度和实际产物后续接执行。
- **专业 Agent 矩阵**：`data_query` 负责语义目录、SQL 检查与数据集；`attribution` 负责可加性变化贡献分解；`anomaly_detection` 负责时序质量、点异常与变化点检测；`visualization` 在 Docker 内生成自包含静态 SVG 图表、JSON 图表配置、交互表格数据与 HTML 报告，前端以可信 React 组件提供表格筛选、排序和分页，并在无脚本 sandbox 中预览经过清洗的 HTML。
- **按 Agent 聚合代码**：`app/agents` 包含 Planner 和四个专业 Agent，每个 Agent 目录聚合自己的构造器、Prompt 与专属 Tools；跨 Agent 协议、注册表、Session 管理和沙盒执行能力位于公共层。
- **Agent 与确定性计算分层**：专业 Agent 负责选择数据、方法和参数，并完成结果解释、下钻与 Repair Request；Tool 负责类型化参数适配和 Agent Session 绑定；`app/analysis` 提供归因、异常检测和可视化的唯一确定性算法实现。运行时将共享 Kernel 源码注入容器，`sandbox_analysis_worker.py` 仅负责受控 I/O、产物写入、响应压缩和操作分发。
- **Session-aware 状态管理**：各 Session 使用 `subagents/{analysis_id}/{agent_type}/{session_id}` 作为 `checkpoint_ns`，状态保存在 PostgreSQL，支持并行分析、服务重启后续接和删除墓碑。
- **产物边界**：Session 产物限定在 `/analyses/{analysis_id}/sessions/{agent_type}/{session_id}/`，共享证据可放入 `/analyses/{analysis_id}/shared/`；结构化结果返回前会校验路径和文件存在性，用户附件上传与删除不能改写该系统目录，会话整体删除仍会统一清理产物。

---

## 部署与安全配置

### 1. 后端环境变量

复制 `conf/.env.example` 为 `conf/.env`，至少配置数据库密码、模型密钥和以下安全变量：

- `JWT_SECRET`：至少 32 字符的高强度随机值，生产环境由密钥管理系统注入。
- `DORIS_QUERY_PASSWORD`：Doris 专用只读分析账号密码，与 `doris_query` 配置一致。
- `DATAAGENT_BOOTSTRAP_ADMIN_USERNAME`、`DATAAGENT_BOOTSTRAP_ADMIN_EMAIL`、`DATAAGENT_BOOTSTRAP_ADMIN_PASSWORD`：仅在执行管理员引导命令时提供。

### 2. Doris 只读账号与 Workload Group

- 在 `conf/app_config.yaml` 中将 `doris_query` 配置为专用查询账号，其 `database` 必须与元数据源 `doris.database` 一致。
- 由 DBA 按当前 Doris 版本创建账号和 `query.workload_group`，仅授予目标库表的查询权限以及使用该 Workload Group 所需的权限；不授予导入、建表、修改、删除、授权或节点管理权限。Doris 版本间授权语法有差异，部署时使用对应版本的官方语法。
- 应用启动时检查 `SHOW GRANTS`、目标数据库可见性并尝试设置 Workload Group，检测到写入/管理权限、目标库不可访问或 Workload Group 不可用时拒绝启动。运行时还会执行 `query` 下的超时、内存、扫描、结果行数与输出字节限制。

### 3. Elasticsearch 索引升级

从未包含 `resource_key` 的旧版本升级时，必须通过元数据同步接口对全部字段语义索引和启用枚举索引的字段执行一次全量重同步。重同步会同时删除新资源键文档和旧的 `t_name` / `c_name` 文档，再写入可用于资产白名单过滤的新文档；全量同步完成前，受限用户不会命中缺少资源键的旧文档。

### 4. 管理员引导

公开注册不会产生 Admin。完成 `conf/.env` 和 PostgreSQL 配置后，显式执行幂等的管理员引导脚本：

```bash
DATAAGENT_BOOTSTRAP_ADMIN_USERNAME=admin \
DATAAGENT_BOOTSTRAP_ADMIN_EMAIL=admin@example.com \
DATAAGENT_BOOTSTRAP_ADMIN_PASSWORD='replace-with-a-strong-password' \
uv run python -m scripts.bootstrap_admin
```

引导完成后从运行环境移除三个 `DATAAGENT_BOOTSTRAP_ADMIN_*` 变量。

公开注册用户初始为 Viewer。Admin 通过 `/api/v1/admin/users/{user_id}/roles` 授予 Analyst 后，用户才能创建会话、上传附件和运行分析。

### 5. 授权撤销与历史留存

角色或资产白名单变更会立即作用于新的目录读取、语义检索、召回读取和 SQL 校验。已写入用户会话的历史消息与分析产物按会话留存策略保存，仍由原会话所有者读取；需要同时清除历史副本时，删除对应会话以级联清理 Checkpoint 和 Docker 工作区。

### 6. 启动、前端代理与 Docker 部署边界

```bash
uv sync
uv run main.py
```

- 后端默认监听 `7000` 端口。Vite 开发代理的 `VITE_APP_PROXY` 默认为 `http://localhost:7000`，可复制 `web/.env.example` 并在非默认部署中覆盖。
- 同一 Docker 主机上的不同部署必须使用唯一的 `sandbox.deployment_namespace`，避免容器和数据卷名称冲突。
- 当前 Docker 会话 UID 注册表更新和文件 mutation lock 使用进程内锁，同一 `deployment_namespace` 只运行一个 API worker；不要为该 namespace 启动多个 Uvicorn/Gunicorn worker。
