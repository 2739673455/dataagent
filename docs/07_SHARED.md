# Shared 模块功能

`shared` 提供没有单一业务归属的配置、外部客户端、数据库基础、错误协议、可观测性和 Celery 运行设施。

## 功能清单

```text
Shared
→ 加载和校验应用配置
→ 管理外部客户端生命周期
→ 提供数据库基础和跨模块契约
→ 统一 HTTP 错误
→ 统一日志和 Trace 上下文
→ 路由和执行后台任务
→ 查询后台任务状态
```

## 1. 加载和校验应用配置

```text
进程启动
→ 读取 conf/.env
→ 读取 conf/app_config.yaml
→ 解析环境变量插值
→ 使用 Pydantic 构造 Cfg
→ 拒绝根配置和嵌套配置中的未知字段
→ 校验每个配置项的类型和范围
→ 校验跨配置约束
  → Celery soft timeout 小于 hard timeout
  → sandbox 单文件上限不超过工作区上限
  → idle_stop 小于 idle_remove
  → 默认模型和各 Specialist 引用已声明模型
  → 模型 Profile 只配置 image_inputs 和 max_input_tokens
  → Responses 模型使用 LangChain OpenAI 原生客户端或 DeepSeek 专属适配
  → 工厂只为支持图片输入的 Responses 模型派生 image_tool_message
→ 配置无效时阻止进程启动
```

配置覆盖 PostgreSQL、Doris、Elasticsearch、Embedding、认证、查询、元数据索引、Celery、生命周期、沙箱、模型、Agent 和 MCP。每个聊天模型显式声明 `chat_completions` 或 `responses` 协议，调用失败时不自动切换协议。数据库密码、JWT 密钥、Embedding 密钥、模型密钥、Redis/Celery URL 以及 MCP URL、Header 和进程环境值使用 `SecretStr` 保存，仅在外部客户端边界解包。

## 2. 管理外部客户端生命周期

```text
FastAPI lifespan 启动
→ 初始化认证、元数据和助手 PostgreSQL manager
→ 初始化 Doris 管理连接和角色查询连接 registry
→ 初始化 Elasticsearch 和 Embedding client
→ 初始化 LangGraph PostgreSQL
→ 上层初始化 sandbox 和 AgentManager

请求或 Service 使用外部资源
→ 从 manager 获取 request-scoped session 或 connection
→ 在 context manager 结束时提交、回滚或释放

应用关闭
→ 先关闭 Agent 和 sandbox
→ 再关闭 LangGraph、Embedding、Elasticsearch
→ 关闭 PostgreSQL 和 Doris 连接池
```

Celery Worker 在任务进程中重新初始化所需客户端，不复用 fork 前连接。

## 3. 提供数据库基础和跨模块契约

```text
SQLAlchemy 模型选择 Base
→ identity 使用 AuthBase
→ metadata、语义召回快照和 query 使用 MetaBase
→ assistant 使用 AssistantBase
→ LangGraph 使用自身 schema

跨模块传递数据
→ Agent 身份和 Session 使用 shared/contracts/analysis.py
→ 查询经验召回结果使用 shared/contracts/query_experience.py
→ 契约只包含消费者需要的字段
→ 契约不暴露 ORM 和 Repository
```

跨 PostgreSQL 数据域只通过稳定 ID 关联，不建立跨数据库外键。

## 4. 统一 HTTP 错误

```text
业务 Service 抛出 ProblemError
→ FastAPI 异常处理器读取 type、title、status、detail 和 extensions
→ 输出 application/problem+json

请求参数校验失败
→ 转换 Pydantic/FastAPI 错误
→ 返回结构化字段位置和原因

出现未处理异常
→ 服务端日志记录完整堆栈
→ 客户端只收到安全的通用错误
```

Agent 工具错误属于模型工具协议，由各工具保留异常类别、字段位置和可修正详情。

## 5. 统一日志和 Trace 上下文

```text
HTTP 请求进入 Trace middleware
→ 创建或读取 trace 标识
→ 绑定请求日志上下文

进入用户、对话或 Agent 运行边界
→ 继续绑定 user_id、conversation_id、analysis_id、session_id、tool_call_id

业务代码写日志
→ 自动携带已经绑定的上下文字段
→ 不需要在每条日志手写 user_id 等重复信息
```

## 6. 路由和执行后台任务

```text
Celery 启动
→ 使用 Redis broker 和 result backend
→ 加载 assistant、metadata、query 和 workflows tasks
→ 只接受 JSON 参数和结果
→ 声明 default、metadata-index、lifecycle、lightweight 队列
→ 禁止自动创建未知队列

提交任务
→ metadata.* 和 query.* 路由 metadata-index
→ assistant.generate_conversation_title 路由 lightweight
→ 其他 assistant.* 和 workflows.* 路由 lifecycle
→ 未匹配任务路由 default

Worker 执行任务
→ started 状态写入 result backend
→ 使用 task_acks_late
→ Worker 丢失时重新投递
→ 应用 soft 和 hard time limit
→ 结果按配置时间过期
```

周期调度：

```text
每日 value_index_sync_time
→ metadata.dispatch_value_indexes

每 lifecycle_schedule_seconds
→ assistant.cleanup_expired_drafts
→ assistant.repair_conversation_titles

每 user_deletion_retry_seconds
→ workflows.dispatch_due_user_deletions 原子领取并提交到期注销任务

每 query_experience_repair_seconds
→ query.repair_indexes
```

需要可靠恢复的业务任务会在业务 PostgreSQL 保存状态，Celery result backend 只承担运行状态观察。

## 7. 查询后台任务状态

```text
API 提交后台任务
→ 返回 TaskAcceptedResponse.task_id

管理员请求 /api/v1/tasks/{task_id}
→ 从 Celery AsyncResult 读取状态
→ 返回 PENDING、STARTED、SUCCESS 或 FAILURE
→ 完成时返回 result
→ 失败时返回 error
```

## 代码位置

```text
配置
→ app/shared/config
→ conf/app_config.yaml

客户端
→ app/shared/clients

数据库和契约
→ app/shared/database
→ app/shared/contracts

错误和可观测性
→ app/shared/errors
→ app/shared/observability

Celery
→ app/shared/tasks

Web 组合根
→ main.py
```
