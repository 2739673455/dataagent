# 07. Workflows：实现可恢复的跨存储注销

## 功能说明

`app/workflows` 负责编排无法在单个业务领域、单个数据库事务中完成的长生命周期持久流程。系统的核心长工作流是**跨存储用户注销**：在安全受理注销后，依次彻底清除用户的会话目录、LangGraph Checkpoint、语义召回快照、Docker 容器与磁盘持久卷，最后物理移除认证用户。

本模块的核心职责与底层实现细节如下。

### 1. 跨模块持久工作流定位与注销受理

用户注销涉及认证数据库（PostgreSQL）、助手图状态库（PostgreSQL Checkpointer）、元数据检索快照（PostgreSQL）、Docker 守护进程（容器实例）以及本地存储（Named Volume）等多个异构系统，无法通过单次数据库 ACID 事务保证原子性。

- **窄接口依赖原则**：
  - Workflows 模块作为无状态编排器，仅通过 `app/workflows/contracts.py` 中定义的最小 Protocol 依赖外部能力：`UserDeletionStateStore`（由 Identity 提供）、`UserSandboxCleaner`（由 Sandbox 提供）与 `ConversationLifecycleService`（由 Assistant 提供）；
  - 严禁在工作流中直接引入其他业务模块的 ORM 模型、SQLAlchemy Session、Docker SDK 或底层 Redis 键，杜绝编排层对具体存储知识的隐式耦合。
- **原子注销受理（request_deletion）**：
  - 管理员通过接口发起注销请求，系统首先执行业务安全约束校验：禁止当前登录的操作员注销自身，核验系统中保留至少一个启用的管理员；
  - 在单个认证数据库事务中执行：将目标用户的 `is_active` 置为 `False`，撤销该名下所有 Refresh Token，并在 `user_deletion_tasks` 表中插入或更新一条 `status='pending'` 的注销任务记录；
  - 事务提交后，目标用户的既有访问令牌和刷新令牌在下一次请求时全部立即失效，平台访问权限瞬间阻断；HTTP 接口随即向客户端返回成功，物理清理交由后台异步流转，保障毫秒级响应。

### 2. 幂等编排与固定清理拓扑

- **固定清理依赖拓扑**：
  - `UserDeletionService.process(user_id)` 按照不可调换的拓扑顺序执行清理：
    ```text
    1. 会话资源清理（ConversationLifecycleService.delete_user_conversations）：
       清理所有 Conversation 行、物理删除 LangGraph Checkpoints、清除语义召回快照；
    2. 沙箱资源清理（UserSandboxCleaner.delete_user_sandbox）：
       停止并销毁用户专属 Docker 容器，删除用户 Named Volume，清理 Redis 所有权键；
    3. 任务完成与用户物理删除（UserDeletionStateStore.complete）：
       物理删除 users 表中的认证记录，将 user_deletion_tasks 标记为 completed。
    ```
- **认证用户作为恢复锚点**：
  - 认证用户 `User` 记录与 `UserDeletionTask` 任务行强制保留至外部异构资源（会话、Checkpoints、Docker 容器与卷）全部清理完毕后才物理删除；
  - 若在清理的任何中间步骤遭遇进程崩溃、Docker API 超时或网络中断，数据库中始终保留着带有 `pending` 状态的任务记录与用户 ID，作为后续自动恢复的确定性锚点。
- **全链路幂等设计**：
  - 各资源清理器（Cleaner）必须将“目标资源已不存在”视为清理成功（例如容器已被删除、卷已不存在或会话已为空）；
  - 工作流在发生重试时一律从第 1 步开始重新执行完整清理流程。由于每个单项步骤均具备严格幂等性，系统无需维护复杂的子步骤完成位，以极低的架构复杂度换取确定性的最终一致性。

### 3. 终态保护与悲观行锁并发控制

- **单向不可逆终态（completed）**：
  - `UserDeletionTask` 的状态机限定为 `pending` 与 `completed` 两种；
  - `completed` 是单向不可逆的绝对终态。一旦任务写入 `completed`，无论后续是否有迟到的重试 Worker 回写错误，数据库状态绝不被回退为 `pending`。
- **行级排他锁（FOR UPDATE）保障并发收敛**：
  - `PostgresUserDeletionStateStore.complete()` 与 `record_failure()` 在执行时，必须在认证事务内通过 `get_user_deletion_task_for_update(user_id)` 锁定任务行；
  - 任何针对已处于 `completed` 状态任务的失败回写操作被直接忽略，防止因分布式消息重投导致的迟到错误覆盖最终成功事实。

### 4. 双层调度、分布式任务租约与自愈机制

系统通过“快速重试”与“慢速自愈”双层调度体系抵御所有阶段的故障。

- **第一层：Celery 快速重试**：
  - 任务投递至 Celery 的 `lifecycle` 队列；
  - Worker 执行遇到瞬时故障（如 Docker 守护进程瞬时高负载）时，利用 Celery 异常重试机制进行就地重试（最多 3 次），配置指数退避与随机抖动（jitter），在秒级时间内完成快速自愈。
- **第二层：Celery Beat 周期自愈分发**：
  - 若 Celery 快速重试耗尽，或 Worker 节点遭遇硬件断电、OOM 强制终止导致消息丢失，数据库中的 `UserDeletionTask` 仍处于 `pending` 状态；
  - Celery Beat 定期（默认每 60 秒）执行 `dispatch_due_user_deletions` 任务，扫描数据库中满足 `status = 'pending' AND next_attempt_at <= now` 的任务。
- **悲观抢占与租约机制（SKIP LOCKED）**：
  - 扫描任务使用 `SELECT ... FOR UPDATE SKIP LOCKED`，多个并发调度器节点可安全并行扫描而不发生行锁冲突，每个节点仅锁定并领取未被锁定的到期记录；
  - 领取成功后，立即在同一事务中将 `next_attempt_at` 推进至 `now + visibility_timeout`（例如 15 分钟后）作为分布式执行租约；
  - 租约完整覆盖了“数据库已领取、Broker 消息发送中”以及“Worker 正在执行但尚未完成”的时间窗口，杜绝其他调度节点在任务正常执行期间重复拉起相同用户；
  - 若任务在执行期间崩溃，租约到期后该任务自动再次对 Beat 变为可见并重新分发。
- **批量分发错误隔离**：分发器批量扫描到多个到期用户时，逐个调用 `enqueue_user_deletion`。若某单个用户的消息发布发生异常，系统仅记录该用户的错误并将该用户的 `next_attempt_at` 推迟，其余用户的消息发布不受任何影响。

### 5. Worker 子进程资源生命周期完全隔离

在异步架构中，父进程在 fork 子进程前创建的异步事件循环、数据库连接池与 Socket 句柄绝不能在子进程中复用。

- **专属运行时动态装配**：
  - Celery Worker 在子进程中执行 `dataagent.workflows.delete_user` 任务时，`_process_user_deletion(user_id)` 动态实例化当前任务专属的资源管理器：
    - 独立的 `PostgresClientManager`（分别连接 `auth_postgresql`、`meta_postgresql`、`langgraph_postgresql`）；
    - 独立的 `LangGraphPostgresManager`；
    - 独立的 `DockerSandboxManager`；
    - 独立的 `AgentManager` 与 `ConversationLifecycleService`。
- **任务级显式初始化与安全逆序释放**：
  - 连接池在任务入口显式执行 `init()`；
  - 业务执行结束后，在 `finally` 代码块中严格按照依赖关系的逆序调用各管理器的 `close()` / `disconnect()` 释放所有网络连接与文件描述符，防止 Worker 长时间运行产生连接泄漏。

---

## 核心实现代码与模块架构

### 1. 跨模块工作流依赖契约实现

文件路径：`app/workflows/contracts.py`

定义工作流所需的最小窄接口，屏蔽底层各模块的存储细节：

```python
# app/workflows/contracts.py
"""跨模块工作流依赖协议。"""

from datetime import datetime
from typing import Protocol


class UserDeletionStateStore(Protocol):
    """用户注销编排所需的认证状态存储能力。"""

    async def request(self, user_id: int, requested_at: datetime) -> bool:
        """在认证库中禁用用户、吊销令牌并创建注销任务。"""
        ...

    async def is_completed(self, user_id: int) -> bool:
        """判断用户注销任务是否处于 completed 终态。"""
        ...

    async def complete(self, user_id: int, completed_at: datetime) -> None:
        """物理删除认证用户并将任务记录置为 completed 终态。"""
        ...

    async def record_failure(
        self,
        user_id: int,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None:
        """记录注销失败原因并推进下一次可重试时间。"""
        ...


class UserSandboxCleaner(Protocol):
    """用户注销所需的沙箱清理能力。"""

    async def delete_user_sandbox(self, user_id: int) -> None:
        """删除用户全部沙箱资源（容器、Volume 与 Redis 键）。"""
        ...
```

### 2. 跨存储用户注销编排服务实现

文件路径：`app/workflows/user_deletion.py`

控制拓扑清理顺序、终态幂等检查与失败退避记录：

```python
# app/workflows/user_deletion.py
"""跨存储用户注销编排服务。"""

from datetime import UTC, datetime, timedelta
from loguru import logger

from app.assistant.services.conversation_lifecycle import ConversationLifecycleService
from app.identity import errors as auth_error
from app.shared.config.app_config import LifecycleConfig
from app.workflows.contracts import UserDeletionStateStore, UserSandboxCleaner


class UserDeletionService:
    """协调认证库、会话库、检索快照和沙箱容器的用户注销。"""

    def __init__(
        self,
        state_store: UserDeletionStateStore,
        sandbox: UserSandboxCleaner,
        conversations: ConversationLifecycleService,
        config: LifecycleConfig,
    ) -> None:
        self._state_store = state_store
        self._sandbox = sandbox
        self._conversations = conversations
        self._config = config

    async def request_deletion(self, user_id: int, *, operator_id: int) -> bool:
        """禁用目标用户并在认证数据库中持久化注销任务。"""
        if user_id == operator_id:
            raise auth_error.InvalidUserMutationError(
                detail="不能注销当前操作的管理员账号"
            )

        submitted = await self._state_store.request(user_id, datetime.now(UTC))
        if submitted:
            logger.info(f"用户注销已受理: operator_id={operator_id}, user_id={user_id}")
        return submitted

    async def process(self, user_id: int) -> None:
        """幂等执行一个用户的跨存储注销清理。"""
        # 1. 终态检查：已完成的任务直接跳过，绝不产生外部调用
        if await self._state_store.is_completed(user_id):
            logger.info(f"用户注销清理已完成，跳过重复任务: user_id={user_id}")
            return

        logger.info(f"开始用户注销清理编排: user_id={user_id}")
        try:
            # 2. 清理会话、LangGraph Checkpoints 与语义召回快照
            await self._conversations.delete_user_conversations(user_id)
            logger.info(f"用户会话资源清理完成: user_id={user_id}")

            # 3. 停止并删除 Docker 容器、物理卷与 Redis 状态
            await self._sandbox.delete_user_sandbox(user_id)
            logger.info(f"用户沙箱资源清理完成: user_id={user_id}")

            # 4. 物理删除认证用户并将任务记录置为 completed 终态
            await self._state_store.complete(user_id, datetime.now(UTC))
            logger.info(f"用户注销清理编排完成: user_id={user_id}")
        except Exception as exc:
            # 任一步失败，记录错误并根据退避配置设置重试时间
            await self._record_failure(user_id, exc)
            logger.exception(
                f"用户注销清理编排失败: user_id={user_id}, error_type={type(exc).__name__}"
            )
            raise

    async def _record_failure(self, user_id: int, exc: Exception) -> None:
        """记录注销失败原因和下一次重试时间。"""
        now = datetime.now(UTC)
        next_attempt_at = now + timedelta(
            seconds=self._config.user_deletion_retry_seconds
        )
        await self._state_store.record_failure(
            user_id,
            error=f"{type(exc).__name__}: {exc}",
            next_attempt_at=next_attempt_at,
        )
```

### 3. 认证状态存储与终态行锁保护实现

文件路径：`app/identity/services/user_deletion_store.py`

展示数据库行级悲观锁如何保护 `completed` 终态不被迟到失败覆盖：

```python
# app/identity/services/user_deletion_store.py（核心终态锁片段）
"""用户注销认证状态存储。"""

from datetime import datetime
from app.identity.repositories.identity import IdentityPGRepo
from app.shared.clients.postgres_client_manager import PostgresClientManager


class PostgresUserDeletionStateStore:
    """使用认证 PostgreSQL 原子维护用户注销状态与终态保护。"""

    def __init__(self, postgres: PostgresClientManager) -> None:
        self._postgres = postgres

    async def complete(self, user_id: int, completed_at: datetime) -> None:
        """物理删除认证用户并将注销任务锁定置为 completed 终态。"""
        async with self._postgres.session() as session:
            repo = IdentityPGRepo(session)
            async with session.begin():
                await repo.lock_security_mutation()
                # 使用 FOR UPDATE 锁定任务行，防止并发冲突
                task = await repo.get_user_deletion_task_for_update(user_id)
                if task is None:
                    raise RuntimeError("用户注销任务记录不存在")
                user = await repo.get_user_by_id_for_update(user_id)
                if user is not None:
                    # 外部资源全部成功后，才最后删除认证用户记录
                    await repo.delete_user(user)
                await repo.complete_user_deletion(task, completed_at)

    async def record_failure(
        self,
        user_id: int,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None:
        """记录注销失败，终态任务绝不回退。"""
        async with self._postgres.session() as session:
            repo = IdentityPGRepo(session)
            async with session.begin():
                task = await repo.get_user_deletion_task_for_update(user_id)
                # 终态保护：若任务已被其他 Worker 标记为 completed，禁止写入失败
                if task is not None and task.status != "completed":
                    await repo.record_user_deletion_failure(
                        task,
                        error=error,
                        next_attempt_at=next_attempt_at,
                    )
```

### 4. Celery Worker 任务与进程级资源生命周期实现

文件路径：`app/workflows/tasks.py`

在子进程中动态创建专属连接池并在 `finally` 逆序释放：

```python
# app/workflows/tasks.py（核心执行与装配）
"""跨存储用户注销后台任务。"""

from datetime import UTC, datetime, timedelta
from loguru import logger

from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.agents.manager import AgentManager
from app.assistant.providers import build_conversation_lifecycle_service
from app.assistant.services.conversation_tombstone_store import ConversationTombstoneStore
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AssistantBase, AuthBase, MetaBase
from app.shared.tasks.celery_app import TASK_VISIBILITY_TIMEOUT_SECONDS, celery_app
from app.shared.tasks.runner import run_async
from app.shared.tasks.submission import TaskSubmission
from app.workflows.user_deletion import UserDeletionService


def enqueue_user_deletion(user_id: int) -> TaskSubmission:
    """提交用户注销清理任务至 lifecycle 队列。"""
    task = celery_app.send_task(
        "dataagent.workflows.delete_user",
        args=[user_id],
        queue="lifecycle",
        routing_key="lifecycle",
    )
    return TaskSubmission(task_id=task.id)


async def _process_user_deletion(user_id: int) -> None:
    """Worker 子进程独立初始化跨存储连接并在任务结束时逆序销毁。"""
    auth_postgres = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    assistant_postgres = PostgresClientManager(cfg.langgraph_postgresql, AssistantBase)
    meta_postgres = PostgresClientManager(cfg.meta_postgresql, MetaBase)
    persistence = LangGraphPostgresManager(cfg.langgraph_postgresql)
    sandbox = create_sandbox_manager(cfg.sandbox, packaged_skill_readonly_mounts())
    agents = AgentManager(persistence, sandbox, ConversationTombstoneStore(assistant_postgres))
    conversations = build_conversation_lifecycle_service(
        persistence, assistant_postgres, meta_postgres, agents, sandbox, cfg.lifecycle
    )
    state_store = PostgresUserDeletionStateStore(auth_postgres)
    service = UserDeletionService(state_store, sandbox, conversations, cfg.lifecycle)

    # 显式初始化子进程专属连接池
    auth_postgres.init()
    assistant_postgres.init()
    meta_postgres.init()
    await persistence.init()
    await sandbox.init(start_cleanup=False)

    try:
        started_at = datetime.now(UTC)
        await state_store.extend_claim(
            user_id,
            lease_until=started_at + timedelta(seconds=TASK_VISIBILITY_TIMEOUT_SECONDS),
        )
        await service.process(user_id)
    finally:
        # 逆序安全释放连接池
        await agents.close()
        await sandbox.disconnect()
        await persistence.close()
        await meta_postgres.close()
        await assistant_postgres.close()
        await auth_postgres.close()


@celery_app.task(
    name="dataagent.workflows.delete_user",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def delete_user(self, user_id: int) -> None:
    """Celery 任务入口。"""
    try:
        run_async(_process_user_deletion(user_id))
    except Exception as exc:
        # 异常就地快速重试，退避参数受 Celery 管理
        raise self.retry(exc=exc)
```

---

## 阶段学习与验证要点

### 阶段 1：验证注销受理与即时失效

1. **自注销拦截验证**：当前管理员向 `/api/v1/admin/users/{current_admin_id}` 发起注销，验证系统立即抛出 `InvalidUserMutationError` 并拒绝操作。
2. **受理即时失效验证**：注销某个普通用户，验证数据库中该用户 `is_active` 立即变为 `False`，且其所有 Refresh Token 的 `revoked_at` 均被写入时间戳，后续携带原令牌访问受保护接口立即返回 401。

### 阶段 2：验证跨异构存储清理拓扑与最终删除

1. **中途异常恢复锚点验证**：在测试用例中注入故障使第 2 步沙箱清理抛出异常，验证执行中断后数据库中的 `UserDeletionTask` 记录状态为 `pending` 且附带 `last_error`，认证用户 `User` 记录依然存在，可被后续 Beat 重新扫描拉起。
2. **全流程成功最终物理删除验证**：在无故障环境下运行完整注销流程，验证执行完毕后：
   - 助手库中的相关会话与 Checkpointer 节点被物理删除；
   - Docker 守护进程中的用户容器与命名卷被彻底删除；
   - 认证库中的 `users` 记录被物理删除，`user_deletion_tasks` 状态更新为 `completed`。

### 阶段 3：验证终态保护与并发排他

1. **迟到失败不回退终态验证**：并发模拟两个执行单元，单元 A 执行成功调用 `complete()`，随后单元 B 调用 `record_failure()`，验证最终任务状态依然保持 `completed`，绝不回退为 `pending`。
2. **分布式租约防重复领取验证**：两个调度节点并发执行 `claim_due_user_deletions`，验证由于 `FOR UPDATE SKIP LOCKED` 机制，相同的 `user_id` 仅被一个节点领取并推进租约时间。
