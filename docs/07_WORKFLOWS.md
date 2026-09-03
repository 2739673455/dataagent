# 07. Workflows 模块职责与实现

`workflows` 负责编排跨多个业务模块和存储、需要持久化恢复的生命周期流程。当前功能是用户注销。

## 模块职责与边界

`workflows` 承担无法在单个数据库事务中完成的跨模块业务流程。当前实现只包含用户注销：先在认证库持久化任务，再依次调用 `assistant` 和 `sandbox` 的公开清理能力，最后删除认证用户并写入完成状态。

平台管理员通过 `identity` 的用户删除接口发起流程；Celery Beat 领取到期任务；Lifecycle Worker 执行清理；`identity`、`assistant` 和 `sandbox` 分别维护自己资源内部的一致性。

该模块不复制各资源的删除细节，也不建立分布式事务。它保存可恢复的任务状态，通过固定执行顺序、幂等清理、任务租约和周期扫描实现最终一致性。

## 功能清单

```text
Workflows
→ 受理用户注销
→ 执行跨存储注销清理
→ 恢复失败或丢失的注销任务
```

## 1. 受理用户注销

**实现目的**

在管理员发起注销时立即撤销目标用户的访问能力，并把后续资源清理记录为不会因 HTTP 请求结束或消息发布失败而丢失的持久任务。

**使用者与使用方式**

- 平台管理员通过 `/api/v1/admin/users/{user_id}` 发起注销。
- 调用方收到 `204` 后即可认为注销请求已受理，无需等待物理资源全部删除。
- 当前操作管理员和最后一个启用管理员受到保护。
- 周期调度器根据认证 PostgreSQL 中的任务状态决定何时执行清理。

**具体实现**

```text
管理员请求注销目标用户
→ 拒绝管理员注销自己
→ identity 禁用目标用户
→ identity 撤销目标用户 Refresh Token
→ identity 创建或复用 pending UserDeletionTask
→ 等待周期调度原子领取
→ 请求返回 204
```

用户在请求阶段已经失去访问能力，后台资源清理可以随后完成。


### 设计细节：受理事务先形成业务事实，消息调度随后发生

HTTP 请求不会直接发送 `delete_user` 消息。Identity 在一个认证 PostgreSQL 事务内锁定安全变更，禁用用户、吊销 Refresh Token 并 upsert `UserDeletionTask`。只有数据库提交成功，请求才返回已受理。

```python
async with session.begin():
    await repo.lock_security_mutation()
    user = await repo.get_user_by_id_for_update(user_id)
    task = await repo.get_user_deletion_task_for_update(user_id)
    if user is None:
        if task is not None and task.status == "completed":
            return False
        raise auth_error.UserNotFoundError
    if user.is_active and user.is_admin and await repo.count_admins() <= 1:
        raise auth_error.LastAdministratorError
    await repo.set_user_active(user, False)
    await repo.revoke_user_refresh_tokens(user.id, requested_at)
    await repo.enqueue_user_deletion(user.id, requested_at)
```

Celery Beat 从这张表发现任务并发布消息。即使 API 进程在事务提交后、发送消息前退出，也不会丢失注销请求。重复受理使用 PostgreSQL `ON CONFLICT DO UPDATE` 把未完成记录恢复为 pending；completed 记录保持终态。

## 2. 执行跨存储注销清理

**实现目的**

按照资源依赖顺序删除用户的 Conversation、Checkpoint、召回快照、沙箱文件和认证记录，避免先删除用户主记录后失去定位其外部资源的依据。

**使用者与使用方式**

- Lifecycle Worker 消费 `dataagent.workflows.delete_user`。
- `assistant` 提供用户级 Conversation 与 Agent 状态清理。
- `sandbox` 提供用户级 Container、Volume 和 ownership 状态清理。
- `identity` 在所有外部资源清理成功后删除 User 并完成任务。

**具体实现**

```text
Worker 接收 user_id
→ 查询注销任务状态
→ completed 时幂等返回
→ 调用 assistant 删除用户全部对话
  → 删除 LangGraph 状态
  → 删除语义召回快照
  → 删除每个会话沙箱目录
  → 删除 Conversation 和 Tombstone
→ 调用 sandbox 删除用户沙箱
  → 阻止新操作并等待已有操作完成
  → 删除用户 Docker 容器
  → 删除用户 Named Volume
  → 保留用户删除墓碑并移除活动时间
→ 调用 identity 完成注销
  → 删除 User 记录
  → 将 UserDeletionTask 标记为 completed
```

工作流只调用各模块公开的生命周期能力，各资源的具体清理规则留在所属模块。


### 设计细节：编排层只依赖各模块公开的清理能力

Workflows 通过窄 Protocol 依赖认证状态和用户沙箱清理，不直接访问 Conversation 表、LangGraph schema、Docker API 或 Redis key。Conversation 的跨存储细节统一封装在 `ConversationLifecycleService`。

```python
class UserDeletionStateStore(Protocol):
    async def request(self, user_id: int, requested_at: datetime) -> bool: ...
    async def is_completed(self, user_id: int) -> bool: ...
    async def complete(self, user_id: int, completed_at: datetime) -> None: ...
    async def record_failure(
        self,
        user_id: int,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None: ...


class UserSandboxCleaner(Protocol):
    async def delete_user_sandbox(self, user_id: int) -> None: ...
```

这使顺序由工作流控制，每个资源内部的锁、墓碑、幂等判断和删除实现由资源所属模块控制。未来某个清理实现变化时，不需要在 Workflows 复制同一套规则。


### 设计细节：固定清理顺序保证最后仍有可靠任务锚点

执行顺序是 Conversation/Agent/Recall → 用户 Sandbox → 认证用户与任务终态。认证 PostgreSQL 中的 `UserDeletionTask` 始终保留到最后，失败时仍可定位重试对象。

```python
async def process(self, user_id: int) -> None:
    if await self._state_store.is_completed(user_id):
        return
    try:
        await self._conversations.delete_user_conversations(user_id)
        await self._sandbox.delete_user_sandbox(user_id)
        await self._state_store.complete(user_id, datetime.now(UTC))
    except Exception as exc:
        await self._record_failure(user_id, exc)
        raise
```

服务没有保存每个外部步骤的独立完成位。重试会从头执行完整顺序，因此所属模块的删除接口必须允许 Conversation、Checkpoint、索引、目录、Container 或 Volume 已经不存在。Sandbox 的用户删除墓碑在物理资源删除后继续保留，迟到的操作无法重新创建 Volume。

## 3. 恢复失败或丢失的注销任务

**实现目的**

覆盖 Worker 中断、Broker 发布失败、任务消息丢失和临时外部系统故障，使注销流程在没有人工逐项补偿的情况下最终继续执行。

**使用者与使用方式**

- Celery Beat 周期执行 `dispatch_due_user_deletions`。
- Worker 自动重试单次执行中的临时异常。
- 开发和运维人员通过 `UserDeletionTask` 的失败原因、尝试次数和下次执行时间排查长期失败。
- 重复任务可以安全执行，已完成记录直接返回。

**具体实现**

```text
任一步执行失败
→ 保存异常类型和原因
→ 增加 attempt_count
→ 设置 next_attempt_at
→ 保持任务 pending
→ 让当前 Celery 任务失败

Beat 周期调用 dispatch_due_user_deletions
→ 使用 PostgreSQL 行锁领取到期的 pending 任务
→ 跳过其他调度器已经锁定的记录
→ 将 next_attempt_at 推进到 Celery 任务租约到期时间
→ 按 cleanup_batch_size 限制数量
→ 为每个 user_id 重新提交 delete_user
→ Worker 每次开始执行或自动重试时续期任务租约
→ Broker 发布失败时记录错误并按 user_deletion_retry_seconds 重新到期
→ 已完成步骤以幂等方式跳过或重复执行
```

所有注销任务统一经过周期调度入口，避免 API 直接提交和补偿扫描并发产生重复任务。任务租约覆盖 Celery Visibility Timeout；Worker 中断或任务消息丢失后，记录会重新到期并被后续周期领取。


### 设计细节：调度器使用行锁跳过和租约避免重复发布风暴

Beat 在事务内查询到期的 pending 记录，使用 `FOR UPDATE SKIP LOCKED` 让多个调度器实例各自领取不同记录，并立即把 `next_attempt_at` 推进到租约结束时间：

```python
result = await self._session.scalars(
    select(UserDeletionTask)
    .where(
        UserDeletionTask.status == "pending",
        UserDeletionTask.next_attempt_at <= now,
    )
    .order_by(UserDeletionTask.next_attempt_at, UserDeletionTask.user_id)
    .limit(limit)
    .with_for_update(skip_locked=True)
)
tasks = list(result)
for task in tasks:
    task.next_attempt_at = lease_until
await self._session.flush()
```

租约长度使用 Celery Visibility Timeout，它比任务 hard time limit 多 300 秒。消息发布成功后，在租约期间其他 Beat 不会再次领取；Worker 开始或自动重试时再次续期。Worker 或 Broker 丢失消息后，租约自然到期，任务重新进入可领取集合。


### 设计细节：发布失败和执行失败使用同一个恢复时钟

调度器逐个发布已领取任务。某个消息发送失败时，立即写入错误和新的 `next_attempt_at`，不会让同批其他用户跟着失败：

```python
for user_id in user_ids:
    try:
        enqueue_user_deletion(user_id)
    except Exception as exc:
        failed_at = datetime.now(UTC)
        await state_store.record_failure(
            user_id,
            error=f"{type(exc).__name__}: {exc}",
            next_attempt_at=(
                failed_at
                + timedelta(seconds=cfg.lifecycle.user_deletion_retry_seconds)
            ),
        )
    else:
        dispatched_count += 1
```

Worker 内部清理异常使用相同的 `record_failure()` 路径。错误文本截断到数据库允许长度，`attempt_count` 增加，任务保持 pending。Celery 自身的三次退避重试提供快速恢复，Beat 的持久扫描覆盖重试耗尽和消息丢失。


### 设计细节：完成与迟到失败通过任务行锁决定终态

完成阶段在认证事务中锁定 `UserDeletionTask`，删除仍存在的 User，再把任务标记为 completed。失败回写同样锁定任务，并明确跳过 completed：

```python
async def record_failure(
    self,
    user_id: int,
    *,
    error: str,
    next_attempt_at: datetime,
) -> None:
    async with self._postgres.session() as session:
        repo = IdentityPGRepo(session)
        async with session.begin():
            task = await repo.get_user_deletion_task_for_update(user_id)
            if task is not None and task.status != "completed":
                await repo.record_user_deletion_failure(
                    task,
                    error=error,
                    next_attempt_at=next_attempt_at,
                )
```

如果两个重复 Worker 同时执行，一个已经完成而另一个随后报错，迟到的失败不能把 completed 改回 pending。重复 Worker 在入口看到 completed 时直接返回；即使入口检查发生在完成前，各删除步骤和最终完成事务也能安全收敛。

## 一致性与任务

```text
一致性
→ 请求阶段先禁用用户
→ 每个清理步骤允许目标已经不存在
→ 完成状态最后写入
→ 失败状态保存在认证 PostgreSQL
→ Celery result 只用于查看任务运行状态

Celery
→ dataagent.workflows.delete_user
→ dataagent.workflows.dispatch_due_user_deletions
→ 路由 lifecycle 队列
```
