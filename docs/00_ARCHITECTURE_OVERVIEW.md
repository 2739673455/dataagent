# DataAgent 架构与功能总览

当前项目划分为 **7 个一级模块**。文档按“模块 → 功能域 → 具体功能 → 处理细节”展示当前实现。

```text
DataAgent
→ identity 身份与授权
  → 登录和刷新会话
  → 修改密码和退出登录
  → 管理用户
  → 管理 Doris 角色、SELECT 权限和行级策略
  → 发起用户注销
→ metadata 元数据
  → 查看和维护表、字段、指标目录
  → 批量导入和导出目录
  → 同步语义索引和字段取值索引
  → 召回语义资源
  → 持续构建、合并和删除 query 上下文
→ query 查询
  → 执行分析查询
  → 记录查询执行历史
  → 沉淀查询经验
  → 检索查询经验
  → 失效和修复查询经验
  → 管理员查看、禁用和删除查询经验
→ assistant 智能助手
  → 管理对话和消息
  → 执行一轮多 Agent 分析
  → 委派、恢复和修补专业 Agent Session
  → 管理召回工具、查询工具、Skill、附件和产物
  → 生成标题和删除对话
→ sandbox 沙箱
  → 准备用户沙箱和 Session 工作区
  → 提供文件工具和命令执行
  → 上传、下载和保存产物
  → 隔离用户、对话和 Agent Session
  → 控制容量、回收空闲容器和删除资源
→ workflows 工作流
  → 受理用户注销
  → 执行跨存储注销清理
  → 恢复失败或丢失任务
→ shared 共享基础设施
  → 加载和校验配置
  → 管理外部客户端
  → 提供数据库基础和共享契约
  → 统一错误、日志和 Trace
  → 路由后台任务并提供任务状态
```

## 模块文档

```text
identity
→ docs/01_IDENTITY.md
metadata
→ docs/02_METADATA.md
query
→ docs/03_QUERY.md
assistant
→ docs/04_ASSISTANT.md
sandbox
→ docs/05_SANDBOX.md
workflows
→ docs/06_WORKFLOWS.md
shared
→ docs/07_SHARED.md
```

## 运行形态

```text
FastAPI Web 进程
→ main.py 组合应用
  → 初始化日志、异常处理、Trace 和 CORS
  → 注册 /api/v1 路由
  → 初始化 PostgreSQL、Doris、Elasticsearch、Embedding 和 LangGraph
  → 初始化 Docker 沙箱和 AgentManager
  → 校验 Doris 查询身份只读权限
  → 关闭时按依赖顺序释放资源

Celery Worker
→ 加载各业务模块 tasks.py
  → metadata-index 队列处理索引和元数据导入
  → lifecycle 队列处理对话和用户生命周期
  → lightweight 队列处理对话标题
  → default 队列承接未单独路由任务

Celery Beat
→ 提交周期任务
  → 字段取值索引调度
  → 草稿和对话删除修复
  → 对话标题修复
  → 用户注销修复
  → 查询经验索引修复
```

## HTTP 入口

```text
/api/v1
→ /auth
  → identity 认证接口
→ /admin
  → identity 用户、角色、权限、行策略管理
→ /meta
  → metadata 目录与索引任务管理
→ /chat
  → assistant 对话、消息和 SSE 流
→ /chat/attachment
  → assistant + sandbox 附件管理
→ /tasks
  → shared Celery 任务状态
```

## 存储归属

```text
认证 PostgreSQL
→ identity
  → 用户、Refresh Token
  → Doris 查询身份和资产授权投影
  → 用户注销任务

元数据 PostgreSQL
→ metadata
  → 表、字段、指标、取值索引状态
  → 语义召回快照
→ query
  → 查询执行、查询经验、经验资产

助手 PostgreSQL
→ assistant
  → 对话、删除墓碑

LangGraph PostgreSQL
→ assistant
  → Planner 与专业 Agent checkpoint、消息和状态

Elasticsearch
→ metadata
  → 字段、指标、字段值检索文档
→ query
  → 查询经验检索文档

Doris
→ identity
  → 角色、查询用户、SELECT 和 Row Policy 实时状态
→ metadata
  → 物理目录校验、字段取值读取
→ query
  → EXPLAIN 和只读 SQL 执行

Redis
→ shared
  → Celery broker 和 result backend
→ sandbox
  → 跨进程运行实例、租约、锁和活动状态

Docker Named Volume
→ sandbox
  → 用户上传文件、查询结果和分析产物
```

## 核心业务链路

```text
用户问题
→ assistant 创建 Planner turn
→ Planner delegation
→ Explorer recall_context
→ metadata 召回字段、字段值、指标和查询经验上下文
→ Explorer execute_sql
→ query 解析身份、Guard、EXPLAIN、执行和 CSV 落盘
→ Analyst 读取 CSV，完成分析、图表和自包含 HTML 报告
→ Reviewer 审查证据和结论
→ Planner 汇总最终回答

元数据变更
→ metadata 校验并更新 meta_version
→ query 按资产键失效查询经验
→ metadata 提交语义索引或取值索引任务
→ Worker 差量同步 Elasticsearch
→ 新召回读取 PostgreSQL 当前目录并过滤 ES 候选

权限变更
→ identity 修改 Doris 权限和 PostgreSQL 投影
→ 收紧权限时轮换 authorization_epoch
→ metadata 召回按当前 AssetAccessPolicy 过滤
→ query Guard 按当前 AssetAccessPolicy 校验
→ query 经验按 role_name + authorization_epoch 隔离

对话删除
→ assistant 写墓碑并阻止新运行
→ Celery 删除 LangGraph 状态和召回快照
→ sandbox 删除会话目录
→ assistant 删除对话记录

用户注销
→ identity 禁用用户并记录注销任务
→ workflows 清理用户全部对话
→ sandbox 删除用户容器和卷
→ identity 标记注销完成
```

## 依赖规则

```text
API
→ Service
→ Repository / 外部能力 Protocol
→ PostgreSQL、Doris、Elasticsearch、Redis、Docker

业务模块
→ 可以依赖 shared
→ 跨模块只使用公开 Service、Protocol 或 shared contract

workflows
→ 只编排跨模块公开能力
→ 不复制各模块内部清理和一致性规则

shared
→ 不依赖业务模块

模型可见数据
→ 使用专门投影
→ 不直接暴露内部版本、排名、索引状态、授权代次和运行元数据

架构调整
→ 从底层真实抽象开始修改
→ 同步修改全部调用方
→ 不保留旧接口别名和兼容转发层
```
