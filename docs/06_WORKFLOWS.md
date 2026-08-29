# Workflows 模块功能

`workflows` 负责编排跨多个业务模块和存储、需要持久化恢复的生命周期流程。当前功能是用户注销。

## 功能清单

```text
Workflows
→ 受理用户注销
→ 执行跨存储注销清理
→ 恢复失败或丢失的注销任务
```

## 1. 受理用户注销

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

## 2. 执行跨存储注销清理

```text
Worker 接收 user_id
→ 查询注销任务状态
→ completed 时幂等返回
→ 调用 analytics 删除用户全部对话
  → 删除 LangGraph 状态
  → 删除语义召回快照
  → 删除每个会话沙箱目录
  → 删除 Conversation 和 Tombstone
→ 调用 sandbox 删除用户沙箱
  → 阻止新操作并等待已有操作完成
  → 删除用户 Docker 容器
  → 删除用户 Named Volume
  → 清理 Redis ownership 状态
→ 调用 identity 完成注销
  → 删除 User 记录
  → 将 UserDeletionTask 标记为 completed
```

工作流只调用各模块公开的生命周期能力，各资源的具体清理规则留在所属模块。

## 3. 恢复失败或丢失的注销任务

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

## 一致性和代码

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

代码
→ app/workflows/contracts.py
→ app/workflows/user_deletion.py
→ app/workflows/tasks.py
→ app/providers.py
→ app/identity/services/user_deletion_store.py
```
