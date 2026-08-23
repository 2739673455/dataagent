# 系统全景架构与模块交互总览

## 1. 架构定位与业务目标

DataAgent 是面向企业数据分析场景的多 Agent 协同系统。系统通过持久化规划器（Planner）、专业子 Agent（探查、分析、审查、可视化）、多租户安全沙盒以及端到端数据权限管控体系，实现从自然语言提问到受控取数、深度归因、代码复核及分析产物交付的闭环数据分析。

```mermaid
flowchart TD
    User([终端用户 / 管理员]) --> Gateway[FastAPI 网关 & 中间件]
    
    subgraph CoreServices [核心支撑服务层]
        Auth[01 认证授权与数据安全]
        Meta[02 元数据资产与语义检索]
        Query[03 安全查询引擎与执行守卫]
        SandboxMgr[05 Docker 多租户沙盒管理器]
    end

    subgraph AgentLayer [04 多 Agent 协同与分析调度]
        Planner[Planner 核心规划器]
        Explorer[Explorer 取数探查 Agent]
        Analyst[Analyst 归因分析 Agent]
        Reviewer[Reviewer 审查核验 Agent]
        Visualizer[Visualizer 可视化 Agent]
    end

    subgraph StorageLayer [基础设施与存储]
        PG[(PostgreSQL\nAuth / Meta / Checkpoints)]
        ES[(Elasticsearch\n全文 / 向量 / 字段值索引)]
        Doris[(Apache Doris\n分析型数据库)]
        Docker[(Docker Runtime\n多用户隔离容器 & Named Volume)]
    end

    Gateway --> Auth
    Gateway --> Meta
    Gateway --> AgentLayer
    
    Planner --> Explorer & Analyst & Reviewer & Visualizer
    Explorer --> Query
    Explorer --> Meta
    Explorer & Analyst & Reviewer & Visualizer --> SandboxMgr
    
    Auth --> PG & Doris
    Meta --> PG & ES
    Query --> Doris
    SandboxMgr --> Docker
    AgentLayer --> PG
```

---

## 2. 模块划分与文档清单

系统当前已落地的核心能力划分为 5 大业务与技术模块：

| 模块文档 | 模块名称 | 核心职责 | 核心组件 / 服务 |
| :--- | :--- | :--- | :--- |
| [`01_AUTH_AND_SECURITY.md`](../docs/01_AUTH_AND_SECURITY.md) | 认证授权与数据安全 | 用户认证、Refresh Token 轮换与撤销、限流防爆破、平台管理员管控、Doris 动态查询身份加密存储、Doris 库表列 SELECT 权限授权与回收、Doris 行级过滤策略管理（`SHOW/CREATE/DROP ROW POLICY`）、数据资产白名单投影 | [`AuthService`](../app/identity/services/auth.py)<br>[`AuthorizationService`](../app/identity/services/authorization.py)<br>[`DorisPermissionService`](../app/identity/services/doris_permission.py)<br>[`DorisCredentialCipher`](../app/identity/services/credential.py) |
| [`02_METADATA_AND_SEARCH.md`](../docs/02_METADATA_AND_SEARCH.md) | 元数据资产与语义检索 | 表/字段/指标元数据全生命周期管理、YAML 格式导入导出与冲突校验、ES 全文/向量/字段值多索引版本同步、多阶段语义召回、拓扑关系补全与召回历史沉淀 | [`MetaCatalogService`](../app/metadata/services/catalog.py)<br>[`MetaImportService`](../app/metadata/services/import_service.py)<br>[`MetaIndexService`](../app/metadata/services/index.py)<br>[`MetaSearchService`](../app/metadata/services/search.py) |
| [`03_QUERY_ENGINE_AND_GUARD.md`](../docs/03_QUERY_ENGINE_AND_GUARD.md) | 安全查询引擎与执行守卫 | 基于用户绑定的 Doris 隔离查询身份受控执行、连接级资源限制（`workload_group`、内存、超时、包大小）、服务端游标流式拉取、基于 AST 语法树的严格只读与越权拦截校验 | [`DorisQueryRepository`](../app/query/repositories/doris.py)<br>[`QueryGuardService`](../app/query/services/guard.py)<br>[`AnalysisQueryService`](../app/query/services/executor.py) |
| [`04_MULTI_AGENT_ANALYTICS.md`](../docs/04_MULTI_AGENT_ANALYTICS.md) | 多 Agent 协同与数据分析 | 基于 DeepAgents 与 LangGraph Checkpoint 的动态子 Agent 架构；Planner 动态调度；Explorer、Analyst、Reviewer、Visualizer 专业分工；基于 `thread_id + checkpoint_ns` 的多维并行与状态持久化；跨 Agent 审查与 `RepairRequest` 回退修补；SSE 实时流式响应 | [`AgentManager`](../app/analytics/agents/manager.py)<br>[`AgentRegistry`](../app/analytics/agents/registry.py)<br>[`AgentSessionService`](../app/analytics/agents/session_service.py)<br>[`ChatService`](../app/analytics/services/chat.py) |
| [`05_DOCKER_SANDBOX_RUNTIME.md`](../docs/05_DOCKER_SANDBOX_RUNTIME.md) | Docker 多租户沙盒运行环境 | 一用户一容器 + 一用户一持久化 Named Volume；会话级 UID/GID 权限隔离（`0700`）；按需启动与空闲超时自动回收；全局并发限制与 FIFO 容量队列；沙盒内代码执行与容量配额管理；附件与分析产物安全传输 | [`DockerSandboxManager`](../app/sandbox/docker_manager.py)<br>[`AttachmentRouter`](../app/analytics/api/attachment/router.py) |

### 2.1 模块化单体目录

后端按照业务能力聚合代码，每个业务模块在内部保留接口、应用服务、仓储和领域模型分层：

```text
app/
├── identity/       # 认证、用户、角色与数据权限
├── metadata/       # 元数据目录、语义检索与索引同步
├── query/          # SQL 守卫、受控执行与查询经验
├── analytics/      # 对话、附件接口与多 Agent 分析编排
├── sandbox/        # Docker 隔离运行时
├── workflows/      # 用户注销等跨模块业务流程
└── shared/
    ├── config/         # 应用与元数据配置
    ├── errors/         # HTTP Problem Details 与全局异常处理
    ├── observability/  # 结构化日志、请求上下文与链路追踪
    ├── database/       # SQLAlchemy 声明基类
    ├── clients/        # PostgreSQL、Doris、ES 等外部资源客户端
    └── contracts/      # 跨模块共享的稳定协议
```

模块内依赖方向统一为 `api → services → repositories / models`。`metadata` 服务通过端口接收查询经验失效能力，避免依赖 `query` 的具体实现；跨多个业务模块的持久化操作由 `workflows` 统一编排。`shared` 不依赖业务模块。

---

## 3. 端到端典型业务链路

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户 / 前端
    participant Gateway as API 网关 (Chat Router)
    participant AuthSvc as 权限服务 (AuthService)
    participant Planner as 规划器 (Planner Agent)
    participant Explorer as 探查器 (Explorer Agent)
    participant SearchSvc as 语义检索 (MetaSearchService)
    participant Guard as 安全守卫 (QueryGuardService)
    participant Doris as Apache Doris
    participant Analyst as 分析器 (Analyst Agent)
    participant Reviewer as 审查器 (Reviewer Agent)
    participant Visualizer as 可视化 (Visualizer Agent)
    participant Sandbox as Docker 沙盒 (SandboxManager)

    User->>Gateway: POST /api/v1/chat/stream（自然语言提问）
    Gateway->>AuthSvc: 校验 JWT Token 并提取用户所属 Doris 角色
    Gateway->>Planner: 启动分析会话 (SSE Stream)
    
    rect rgb(240, 248, 255)
        note over Planner, Explorer: 阶段一：目标拆解与数据获取
        Planner->>Explorer: 动态委派取数任务 (delegate_agent)
        Explorer->>SearchSvc: 检索相关指标与表元数据 (语义召回)
        SearchSvc-->>Explorer: 返回授权范围内的表结构与指标口径
        Explorer->>Guard: 提交拟执行 SQL 进行安全合规审计
        Guard->>Guard: AST 语法树解析 (阻断 DDL/DML/越权表)
        Guard-->>Explorer: 审计通过 (附加资源限制参数)
        Explorer->>Doris: 使用用户专属代理账号执行 SQL
        Doris-->>Explorer: 返回数据流
        Explorer->>Sandbox: 将原始数据集保存为 CSV 文件并输出数据摘要
        Explorer-->>Planner: 返回数据集引用与字段画像
    end

    rect rgb(255, 250, 240)
        note over Planner, Analyst: 阶段二：归因与统计分析
        Planner->>Analyst: 动态委派归因分析 (支持按多维度并行派生 Session)
        Analyst->>Sandbox: 读取数据集，编写并运行 Python 计算脚本
        Sandbox-->>Analyst: 输出分析结果、维度贡献率与统计特征
        Analyst-->>Planner: 提交初步归因结论与分析中间产物
    end

    rect rgb(255, 240, 245)
        note over Planner, Reviewer: 阶段三：独立核验与修补闭环
        Planner->>Reviewer: 委派审查任务 (核验 SQL 口径与计算逻辑)
        Reviewer->>Sandbox: 独立运行核验脚本，校验指标一致性
        alt 发现口径偏差或计算缺陷
            Reviewer-->>Planner: 返回结构化 RepairRequest 修补请求
            Planner->>Explorer: 唤醒原 Session 续接修复取数口径
            Explorer-->>Planner: 更新数据集
        else 核验通过
            Reviewer-->>Planner: 审查通过确认
        end
    end

    rect rgb(240, 255, 240)
        note over Planner, Visualizer: 阶段四：产物渲染与汇总响应
        Planner->>Visualizer: 委派图表生成与报告排版
        Visualizer->>Sandbox: 读取分析产物，生成静态图表与报告文件
        Visualizer-->>Planner: 返回图表、数据文件与报告附件引用
        Planner-->>Gateway: 汇总多 Agent 产物并完成最终回答
        Gateway-->>User: SSE 持续输出完整分析报告、图表及附件引用
    end
```

---

## 4. 技术栈与架构原则

- **后端运行时**：Python 3.14 + FastAPI + Uvicorn + Loguru
- **Agent 编排与状态存储**：LangChain + LangGraph + PostgreSQL AsyncSession (`checkpointer=PostgresSaver`)
- **数据源与执行引擎**：Apache Doris（分析型数仓，只读代理身份隔离 + Workload Group 配额）
- **搜索引擎**：Elasticsearch 8.x（文本 BM25 检索 + 稠密向量 KNN 检索 + 字段值模糊检索）
- **环境隔离沙盒**：Docker Engine + Docker SDK + Named Volumes（会话 UID/GID 权限隔离 + FIFO 并发队列）
