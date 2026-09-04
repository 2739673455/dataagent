# 06. Assistant：从单轮对话到可恢复多 Agent 系统

## 功能说明

`app/assistant` 是问数系统的核心编排层。它管理用户会话目录和消息安全投影，创建并管理 Planner、Explorer、Analyst、Reviewer 四大专业智能体运行时，把用户的复杂自然语言提问拆解为可并行、可自愈、可恢复的专业分析任务流，通过 Server-Sent Events（SSE）向前端实时推送思考与执行细节，并治理分析产物与生命周期。

本模块的核心职责与底层实现细节如下。

### 1. 会话目录管理与消息持久化投影

会话元数据与执行图状态分层存储：PostgreSQL 关系型数据库保存会话元数据事实，LangGraph Checkpointer 保存完整的图执行节点快照。

- **多租户隔离的会话模型**：
  - `Conversation` 模型（定义于 `app/assistant/models/conversation.py`）由 `AssistantBase` 声明，记录主键 `id: UUID`、所属用户 `user_id`、会话标题 `title`、草稿标记 `is_draft`、软删除标记 `deletion_requested_at` 及时间戳；
  - 增删改查操作必须强制复合校验 `(user_id, conversation_id)`，严格阻断跨租户越权。
- **LangGraph Checkpointer 线程与命名空间架构**：
  - 全局线程标识：`thread_id = f"user_{user_id}:conversation_{conversation_id}"`；
  - **Planner 根命名空间**：Planner 作为顶层编排者，运行在根 Checkpoint 命名空间（`checkpoint_ns = ""`）；
  - **Specialist 独立子命名空间**：各专业智能体（Explorer、Analyst、Reviewer）运行在由 `AgentSessionKey` 稳定派生的命名空间：`subagents/{analysis_id}/{agent_type}/{session_id}`。子图状态与父图状态完全物理隔离，子图配置显式清除父图的 `checkpoint_id` 与草稿本，保证各子智能体状态自洽。
- **严格消息投影与安全脱敏（MessageProjectionService）**：
  - 客户端请求读取消息历史时，系统首先核验证书归属，随后从 Checkpointer 读取原始消息列表，通过白名单过滤投影为 `PublicMessage`；
  - **公开字段**：用户输入的文本与图片、智能体生成的公开回复、思考过程（`thinking`）、工具调用名称与参数、工具执行结果摘要以及子智能体活动状态；
  - **脱敏拦截**：系统的 `SystemMessage`、大模型内部自愈纠错消息、Provider 专有字段、隐藏的元数据召回底层记录及中间结构化控制字段，全面禁止进入公开 HTTP 响应。
- **异步标题生成与 CAS 更新**：
  - 用户发送首条提问后，系统截取前 20 字符生成临时标题并立即保存，保障前端即时展示；
  - 随后向 Celery 的 `lightweight` 队列投递异步任务，调用轻量级小模型总结生成精炼标题；
  - 任务通过数据库 CAS（Compare-And-Set）机制回写标题：若用户在此期间已手动重命名会话，异步任务放弃覆盖，保留用户主动设定值。

### 2. 多模型工厂与四大专业智能体职责分工

系统基于 `model_factory.py` 屏蔽底层 LLM 差异，并严格划分专业智能体权责。

- **多协议适配与客户端加固**：
  - 支持三种 API 协议：OpenAI Chat Completions、OpenAI Responses 与 OpenRouter；
  - 适配层强制关闭 SDK 内置的无边界重试机制，统一开启流式传输（streaming）；
  - 单次流式响应过程中的所有 chunk 统一绑定稳定的内部消息 ID，杜绝多 Provider 协议碎片泄漏至业务编排层。
- **四大专业智能体职责与工具挂载**：
  - **Planner（主规划智能体）**：
    - 顶层编排者，负责需求拆解、并行委派、监控进度并综合生成最终答复；
    - **工具挂载边界**：严禁直接挂载元数据搜索、SQL 执行等底层数据操作工具，仅持有三个受控会话管理工具：`delegation`（委派任务）、`list_sessions`（查询现有子智能体会话）、`delete_session`（清理指定子智能体会话）；
    - 支持代码化工具调用（Programmatic Tool Calling，PTC）：内置 QuickJS 沙箱环境，支持 Planner 编写 JavaScript 脚本通过 `Promise.all` 并行委派多个独立的子任务。
  - **Explorer（数据探索专家）**：
    - 专精于语义元数据发现与 SQL 数据提取；
    - 独占挂载 `semantic_recall`（元数据语义召回）与 `execute_sql`（Doris 只读 SQL 执行）工具；
    - 负责确认相关物理表、字段血缘，执行只读查询，并在沙箱中生成规整的 CSV 数据产物与样例摘要。
  - **Analyst（深度分析专家）**：
    - 专精于数据计算、统计建模与可视化图表绘制；
    - 挂载沙箱 Python 脚本执行工具、Shell 运行环境及预置分析技能包（packaged skills），直接读取 Explorer 生成的 CSV 文件，输出图表图片与分析结论。
  - **Reviewer（交叉审计专家）**：
    - 专精于逻辑一致性与数据口径交叉复核；
    - 挂载只读文件查看器，复核 Explorer 产出的 SQL 逻辑与 Analyst 得出的分析结论，输出复核报告。
- **严格结构化输出契约（SpecialistResult）**：所有专业智能体在完成委派任务时，必须返回符合严格 Pydantic 约束的 `SpecialistResult`：
  - `status` 限定为 `"completed"`、`"needs_repair"` 或 `"failed"`；
  - `needs_repair` 状态强制要求附带至少一个具体的 `RepairRequest`（包含目标智能体类型、会话 ID 与修补预期）；
  - `failed` 状态强制要求附带明确的失败原因列表；
  - 未知字段与格式畸变直接触发校验失败并进入单次受限自愈修复流程。

### 3. 动态文件上下文与敏感展开中间件

每个智能体在沙箱中有独立的目录视图，系统通过中间件完成运行时动态注入。

- **专属沙箱目录与只读技能**：
  - 每个 Specialist 绑定独立的 `AgentSessionKey`，沙箱后端严格限制其仅能访问属于自身 Session 的工作目录 `/data/{conv_id}/sessions/{key}/`；
  - 外部 Packaged Skills 目录以只读卷方式注入容器，智能体可执行技能脚本但禁止修改自身技能。
- **用户消息中间件（UserMessageContextMiddleware）**：
  - 用户的自然语言输入与上传的附件引用在 Checkpoint 中仅以结构化元数据（`additional_kwargs`）保存；
  - 在调用大模型前，中间件动态拦截请求，从沙箱真实目录读取文件内容，将其安全展开为文本块或经过尺寸限制的图片块传入模型上下文。
- **召回快照二次鉴权中间件**：
  - Explorer 执行完元数据检索后，ToolMessage 中仅持久化检索快照 ID（`recall_id`）；
  - 每次调用大模型前，中间件重新读取快照并应用当前用户的实时 `AssetAccessPolicy`，过滤掉在历史会话之后被管理员回收的资产，杜绝过期 Checkpoint 泄露数据。

### 4. Specialist Session 状态恢复与委派机制

- **Session 生命周期定位**：`AgentSessionKey` 统一决定了 LangGraph Checkpoint 的子命名空间和 Docker 容器内部的文件工作区，实现持久化状态与物理文件的双向对齐。
- **状态恢复与会话续接**：
  - 当 Planner 调用 `delegation` 且传入既有的 `session_id` 时，系统在会话锁保护下加载该 Session 历史的所有 Checkpoint 状态，恢复既有消息流继续执行；
  - 若传入全新的 `session_id`，系统在独立的空命名空间和全新文件目录下初始化子图；
  - 多个不同 `session_id` 的委派任务支持并行并发执行。
- **委派幂等性与 CAS 容错**：
  - 每次委派操作分配唯一的 `delegation_id`；
  - 若发生重试，系统首先检查 Checkpoint 中是否已记录该 `delegation_id` 的执行结果，命中则直接返回已有结果，防止重复调用大模型；
  - 针对大模型返回格式错误，系统仅向子图追加一次带有具体验证错误说明的内部修复提示词（repair message），防止模型无限循环失败。

### 5. 运行时池、会话锁与并发执行

- **单会话独占运行时（RuntimeFactory）**：`RuntimeFactory` 为每个活跃 Conversation 组装完全独立的运行时资源（包括 DockerSandboxBackend、SessionStore、ShellJobRuntime、Planner Graph）。
- **LRU 缓存与并发构建合并（AgentManager）**：
  - 单个 API 进程维护 LRU 内存缓存池；
  - 针对同一会话的高频请求复用内存运行时，当并发到达多个相同会话构建请求时，系统自动合并为单次构建；
  - 内存淘汰仅释放内存对象，绝不删除底层 PostgreSQL Checkpoint 或沙箱磁盘文件。
- **会话分布式咨询锁（Conversation Advisory Lock）**：
  - 整个 Planner 回合在执行前，必须取得绑定在专属 PostgreSQL 物理连接上的会话咨询锁；
  - 进程内由 `asyncio.Lock` 拦截单机并发，数据库端由 `pg_try_advisory_lock` 拦截跨多实例并发；
  - 执行期间将当前的 `asyncio.Task` 登记到全局运行表，支持前端主动取消与删除流程的优雅中断。

### 6. 流式通信与任务恢复（Run、SSE & Resume）

- **执行协程与 HTTP 连接完全解耦（ConversationRunService）**：
  - 后台智能体编排任务运行在独立的后台 `asyncio.Task` 中；
  - 前端客户端断开连接时，仅注销当前 HTTP 连接对应的 SSE 队列消费者，后台分析任务持续执行直至终态；
  - 客户端通过发送显式的 `/cancel` 请求，才会触发取消事件并优雅终止后台 Task。
- **全要素事件流协议**：SSE 实时推送全链路结构化事件：
  - `thinking`：Planner 与各子智能体的思考增量输出；
  - `delta`：最终用户可见回复文本增量；
  - `tool_call` 与 `tool_result`：工具执行进度；
  - `subagent_activity`：各 Specialist 的启动、状态流转与执行结果。
- **断线重连与崩溃恢复（Subscribe 与 Resume）**：
  - **Subscribe**：客户端网络抖动断开后重新连接，若当前内存中存在活跃的 Run，系统利用环形事件缓冲区（Ring Buffer）回放断线期间积压的事件，无缝续接流式；
  - **Resume**：若 API 进程遭遇重启导致内存 Run 丢失，客户端调用 `/resume` 接口，系统扫描 PostgreSQL Checkpoint 中处于未完成状态的挂起图节点并重新拉起协程恢复执行。

### 7. 公开产物治理与跨存储删除工作流

- **产物指令识别与受控公开**：
  - 最终回复中仅允许通过标准指令暴露分析产物：`::artifact{path="sessions/.../report.png"}::`；
  - 系统提取路径后，强制核验该文件真实存在于当前会话的 `sessions/` 目录下、符合合法格式与大小限制，并在会话中注册为公开附件（Attachment），生成带签名的安全下载地址；
  - 任何试图暴露会话根目录外部文件或未生成文件的指令均被静默剥离。
- **跨存储物理删除机制**：
  - 用户调用删除接口时，API 首先取消正在运行的活跃 Run，获取会话生命周期锁，在关系数据库中将 `deletion_requested_at` 打标，使该会话对用户界面立即不可见；
  - 随后将物理清理任务交由 Celery 的 `lifecycle` 队列异步执行；
  - 异步 Worker 依次写入 Agent 墓碑标记，物理清除 Checkpointer 数据、语义召回快照，删除沙箱容器内的会话目录，最后物理删除会话主表记录。重复删除已不存在的资源视为幂等成功。

---

## 核心实现代码与模块架构

### 1. 会话目录持久化模型实现

文件路径：`app/assistant/models/conversation.py`

```python
# app/assistant/models/conversation.py
"""会话目录关系模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import AssistantBase


class Conversation(AssistantBase):
    """助手会话目录模型。"""

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    is_draft: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    create_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    update_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_conversations_user_update", "user_id", "update_at"),
        Index(
            "ix_conversations_pending_deletions",
            "deletion_requested_at",
            postgresql_where=text("deletion_requested_at IS NOT NULL"),
        ),
    )
```

### 2. 委派与智能体输出协议契约实现

文件路径：`app/assistant/agents/contracts.py`

```python
# app/assistant/agents/contracts.py（核心协议定义）
"""Dynamic Subagents 的公共协议。"""

from typing import Annotated, Literal, Self
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from app.shared.contracts.analysis import AgentType


class StrictProtocolModel(BaseModel):
    """拒绝未知字段的严格协议模型基类。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class DelegationRequest(StrictProtocolModel):
    """Planner 发起专业 Agent 委派的请求载荷。"""

    analysis_id: str = Field(min_length=1, max_length=64)
    agent_type: AgentType
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1)


class RepairRequest(StrictProtocolModel):
    """下游 Session 向 Planner 报告的上游修补需求。"""

    target_agent_type: AgentType
    target_session_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1)
    expected_result: str = Field(min_length=1)


class SpecialistResult(StrictProtocolModel):
    """所有专业 Agent 必须返回的严格结构化输出。"""

    status: Literal["completed", "needs_repair", "failed"]
    content: str = Field(min_length=1)
    artifacts: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    repair_requests: list[RepairRequest] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        """校验状态与结果载荷的强一致性。"""
        if self.status == "needs_repair" and not self.repair_requests:
            raise ValueError("needs_repair 状态必须包含至少一个 repair_requests")
        if self.status != "needs_repair" and self.repair_requests:
            raise ValueError("非 needs_repair 状态不得包含 repair_requests")
        if self.status == "failed" and not self.failure_reasons:
            raise ValueError("failed 状态必须包含至少一个 failure_reasons")
        if self.status != "failed" and self.failure_reasons:
            raise ValueError("非 failed 状态不得包含 failure_reasons")
        return self
```

### 3. Planner Agent 顶层编排图构造实现

文件路径：`app/assistant/agents/planner/agent.py`

挂载 QuickJS 代码解释器中间件与受限委派工具：

```python
# app/assistant/agents/planner/agent.py
"""Planner Agent 构造器。"""

from collections.abc import Sequence
from typing import Any, cast
from deepagents import FilesystemMiddleware, create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.middleware.eval_delegations import EvalDelegationMiddleware
from app.assistant.agents.middleware.message_timestamp import MessageTimestampMiddleware
from app.assistant.agents.middleware.user_message_context import UserMessageContextMiddleware
from app.assistant.agents.session_service import AgentSessionService
from app.assistant.agents.shell_jobs import ShellJobRuntime
from app.sandbox.backend import DockerSandboxBackend

PLANNER_SYSTEM_PROMPT = """你是一个顶层数据分析规划智能体（Planner）。
你的核心任务是拆解用户的问数需求，并通过调用 `delegation` 工具委派给专业智能体执行：
- Explorer：探索元数据目录与执行 Doris SQL 查询；
- Analyst：在沙箱运行 Python 数据分析与绘制可视化图表；
- Reviewer：只读审计与复核数据结论。
严禁自行编造数据。对于相互独立的分析任务，你可以编写 JavaScript 脚本通过 `Promise.all` 并发委派。
"""


def create_planner_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
    session_service: AgentSessionService,
    shell_jobs: ShellJobRuntime,
    interpreter_memory_limit_bytes: int = 64 * 1024 * 1024,
) -> CompiledStateGraph:
    """编译包含 QuickJS 代码解释器与受控工具的 Planner 状态图。"""
    # 启用 PTC 模式，仅允许在解释器中调用 delegation 工具
    interpreter = CodeInterpreterMiddleware(
        mode="thread",
        ptc=["delegation"],
        timeout=float("inf"),
        memory_limit=interpreter_memory_limit_bytes,
        max_ptc_calls=None,
    )
    filesystem = FilesystemMiddleware(
        backend=backend,
        tools=["read_file"],
    )
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=PLANNER_SYSTEM_PROMPT,
        middleware=[
            EvalDelegationMiddleware(session_service),
            filesystem,
            interpreter,
            UserMessageContextMiddleware(backend, backend.conversation_dir, shell_jobs),
            MessageTimestampMiddleware(),
        ],
        subagents=[],
        backend=backend,
        checkpointer=checkpointer,
        name="planner",
    )
```

### 4. 委派工具实现

文件路径：`app/assistant/agents/planner/tools/delegation.py`

```python
# app/assistant/agents/planner/tools/delegation.py（核心实现）
"""专业 Agent 委派工具。"""

from typing import Annotated
from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.assistant.agents.contracts import DelegationRequest
from app.assistant.agents.session_service import AgentSessionService
from app.shared.contracts.analysis import AgentType


def create_delegation_tool(service: AgentSessionService) -> BaseTool:
    """创建绑定当前会话的 delegation Tool。"""

    @tool("delegation")
    async def delegation(
        runtime: ToolRuntime,
        analysis_id: Annotated[str, "分析标识符，1-64 位字母数字及下划线连字符"],
        agent_type: Annotated[AgentType, "专业智能体类型：explorer | analyst | reviewer"],
        session_id: Annotated[str, "专业 Session 标识，首次创建或修补时必须复用"],
        message: Annotated[str, "交给专业 Agent 的任务指令与约束"],
    ) -> dict[str, object]:
        """创建或恢复专业 Agent Session 并返回结构化结果。"""
        try:
            request = DelegationRequest(
                analysis_id=analysis_id,
                agent_type=agent_type,
                session_id=session_id,
                message=message,
            )
        except ValidationError as exc:
            return {
                "status": "failed",
                "content": f"委派参数格式错误: {exc.errors()}",
                "failure_reasons": ["参数校验未通过"],
            }

        delegation_id = runtime.tool_call_id or "root_delegation"
        # 调用会话服务执行或续接子图
        result = await service.execute_delegation(
            delegation_id=delegation_id,
            request=request,
            activity_writer=runtime.stream_writer,
        )
        return result.model_dump()

    return delegation
```

### 5. 专业智能体（Specialist）通用状态图构造实现

文件路径：`app/assistant/agents/specialist_agent.py`

强制要求输出 `SpecialistResult` 结构化对象：

```python
# app/assistant/agents/specialist_agent.py（核心构造器）
"""专业 Agent 的共用构造逻辑。"""

from collections.abc import Sequence
from pathlib import Path
from deepagents import create_deep_agent
from deepagents.graph import DeepAgentState
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.contracts import SpecialistResult
from app.assistant.agents.filesystem import build_specialist_filesystem
from app.assistant.agents.middleware.message_timestamp import MessageTimestampMiddleware
from app.assistant.agents.middleware.user_message_context import UserMessageContextMiddleware
from app.assistant.agents.shell_jobs import ShellJobRuntime
from app.sandbox.backend import DockerSandboxBackend


def create_specialist_agent(
    *,
    name: str,
    system_prompt: str,
    skill_directory: Path,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
    shell_jobs: ShellJobRuntime,
    skills: Sequence[str],
) -> CompiledStateGraph:
    """编译共享沙箱文件隔离与 Shell 生命周期的专业 Agent。"""
    resolved_backend, filesystem = build_specialist_filesystem(
        backend,
        skill_directory,
        skills,
    )
    # 绑定结构化输出 schema，强制大模型以 SpecialistResult 格式返回
    structured_model = model.with_structured_output(SpecialistResult)

    return create_deep_agent(
        model=structured_model,
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=[
            filesystem,
            UserMessageContextMiddleware(
                resolved_backend,
                backend.conversation_dir,
                shell_jobs,
            ),
            MessageTimestampMiddleware(),
        ],
        backend=resolved_backend,
        skills=list(skills),
        checkpointer=checkpointer,
        name=name,
    )
```

### 6. 解耦的会话 Run 与 SSE 流式订阅实现

文件路径：`app/assistant/services/conversation_run.py`

```python
# app/assistant/services/conversation_run.py（核心流式运行控制）
"""会话异步 Run 与 SSE 事件分发。"""

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass
class RunEvent:
    event_type: str
    data: dict[str, Any]


class ConversationRun:
    """单个会话的后台执行实体，与 HTTP SSE 连接完全解耦。"""

    def __init__(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        self._history = deque(maxlen=1000)  # 环形事件缓冲区，供重连回放
        self._subscribers: list[asyncio.Queue[RunEvent | None]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self.cancel_event = asyncio.Event()

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """向所有连接的 SSE 队列广播事件并存入环形缓冲区。"""
        event = RunEvent(event_type=event_type, data=data)
        async with self._lock:
            self._history.append(event)
            for queue in self._subscribers:
                await queue.put(event)

    async def subscribe(self) -> AsyncGenerator[RunEvent, None]:
        """前端 SSE 连接订阅事件流，断线重连时先回放历史缓冲。"""
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()
        async with self._lock:
            # 回放历史
            for event in self._history:
                await queue.put(event)
            self._subscribers.append(queue)

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            async with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)
```

---

## 阶段学习与验证要点

### 阶段 1：验证会话归属与消息脱敏投影

1. **跨用户会话读取阻断验证**：用户 A 尝试请求 `/api/v1/chat/conversations/{conv_b_id}`，验证系统返回 404 或 403，多租户逻辑严格生效。
2. **SystemMessage 过滤验证**：检查消息列表接口返回的 JSON 结构，验证包含内部规则与指令的 `SystemMessage` 被完全过滤，客户端仅接收到规范的 User 与 Assistant 消息。

### 阶段 2：验证 Planner 委派与 PTC 并行执行

1. **Planner 工具边界验证**：审查 Planner 提示词与工具清单，验证其未挂载任何直接的数据库查询工具，仅允许通过 `delegation` 发起委派。
2. **QuickJS PTC 并行委派验证**：向智能体提问“分别查询 2023 年和 2024 年的销售额”，观察日志验证 Planner 生成包含 `Promise.all` 的脚本，同时拉起两个 Explorer Session 并行执行。

### 阶段 3：验证 SSE 断线重连与 Run 独立性

1. **客户端断连任务不终止验证**：在智能体开始分析后，强制关闭客户端浏览器窗口，观察服务端日志验证后台 `ConversationRun` 持续执行直至产物生成完毕。
2. **重连事件回放验证**：重新打开浏览器连接会话，调用订阅接口，验证前端通过 Ring Buffer 完整接收到此前错过的思考与增量回复事件。
