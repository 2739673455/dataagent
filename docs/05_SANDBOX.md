# Sandbox 模块功能

`sandbox` 为 Agent 提供受限 Docker 执行环境和持久化文件工作区，并隔离不同用户、对话和专业 Agent Session。

## 功能清单

```text
Sandbox
→ 准备用户沙箱和 Session 工作区
→ 提供文件工具和命令执行
→ 上传、下载和保存产物
→ 隔离用户、对话和 Agent Session
→ 控制运行容器容量
→ 回收空闲容器
→ 删除对话和用户沙箱
→ 挂载只读 Agent Skill
```

## 1. 准备用户沙箱和 Session 工作区

```text
第一次访问某个用户沙箱
→ 获取或创建该用户唯一 Named Volume
→ 获取或创建该用户唯一 Docker 容器
→ 校验 deployment、user label、卷驱动和容器规格
→ 申请全局运行容器容量
→ 启动容器

第一次访问某个对话
→ 在 /workspace/conversations/{conversation_id} 创建目录
→ 为对话分配稳定 UID/GID
→ 写入卷内 UID 注册表

第一次访问某个专业 Agent Session
→ 创建 analyses/{analysis_id}/sessions/{agent_type}/{session_id}
→ 为 Session 分配独立执行 UID
→ 使用对话 GID 设置组读取权限
→ 返回绑定该目录的 DockerSandboxBackend
```

每个用户拥有一个容器和一个持久化卷。容器停止或重建时文件仍保存在卷中。

工作区结构：

```text
/workspace/conversations/{conversation_id}
→ uploads
→ analyses/{analysis_id}/sessions/{agent_type}/{session_id}

/workspace/.dataagent-staging
→ 文件安全提交暂存区

/workspace/.dataagent-activity.json
→ 最近活动时间
```

## 2. 提供文件工具和命令执行

```text
Agent 调用文件工具
→ 将虚拟路径解析到当前会话
→ 读取允许访问同一会话文件
→ 写入、编辑和删除限制在当前 Session
→ 执行 read、write、edit、delete、ls、glob 或 grep
→ 将容器路径转换回 Agent 可见路径

Agent 调用 execute
→ 在当前 Session workspace_dir 中运行命令
→ 受控包装进程以 Session UID 和对话 GID 启动独立进程组
→ stdout 和 stderr 合并写入 large_tool_results/shell_jobs/{job_id}.log
→ 前台固定等待 60 秒，未结束时返回 running 并继续后台执行
→ 日志超过 max_file_bytes 后继续排空输出并标记 output_truncated
→ get_shell_job 查看或等待，cancel_shell_job 先 TERM 后 KILL 整个进程组
```

Specialist Shell Job 没有固定总执行时限。`internal_command_timeout_seconds` 只限制 `du`、限长文件读取、内部编辑脚本和产物检查等同步辅助命令。每个后台任务从启动到终态持续持有 Sandbox operation，因此空闲回收不会停止正在运行任务的容器。

Shell Job Registry 只属于当前 Specialist Agent Run。Run 正常返回、失败或取消时会取消所有未结束任务、等待监控结束并删除 staging 控制文件；Session 工作区中的任务日志继续保留。

容器根文件系统只读，`/workspace` 是持久化读写卷，`/tmp` 是 `nosuid,nodev` tmpfs。容器删除全部 capabilities、启用 `no-new-privileges`，并限制网络、CPU、内存和 PID。

## 3. 上传、下载和保存产物

```text
用户上传文件
→ 规范化外部文件名
→ 自动添加 uploads/ 前缀
→ 流式写入 staging
→ 校验单文件大小和目标 UID
→ 计算提交后的工作区容量
→ 原子移动到最终路径

Explorer 保存查询结果或 Agent 保存产物
→ 明确目标 Session scope
→ 使用 Session UID/GID 写入
→ 执行单文件和工作区容量限制
→ 返回稳定文件路径

调用方下载文件
→ 校验文件存在且是普通文件
→ 校验文件 UID 属于当前会话
→ 校验最大下载大小
→ 通过 Docker archive 流式读取
```

Archive Store 可以访问停止容器挂载的卷，因此上传、下载和清理不需要为了文件操作长期运行容器。

## 4. 隔离用户、对话和 Agent Session

```text
用户隔离
→ 每个 user_id 使用独立容器和 Named Volume

对话隔离
→ 每个 conversation 使用独立根目录和 UID/GID
→ 路径不能跨到其他 conversation

Session 隔离
→ 每个 Agent Session 使用独立执行 UID 和可写目录
→ 当前 Agent 只能修改自己的 Session
→ 当前 Agent 可以读取同一对话其他 Session 的产物

路径校验
→ 拒绝 NUL、控制字符和 ~
→ 拒绝 .. 点段和越级路径
→ 限制总路径和单个路径分量长度
→ 附件删除只能操作 uploads 前缀
```

所有沙箱操作同时进入进程内 `LifecycleGuard` 和跨进程 ownership operation。删除流程进入 maintenance 后，会阻止新操作并等待已有操作完成。

## 5. 控制运行容器容量

```text
已停止用户需要启动容器
→ 检查 max_running_containers
→ 有空位时为该用户保留容量
→ 无空位时进入 FIFO 等待队列
→ 限制最大等待人数和等待时间
→ 容量可用后启动容器并确认占位

等待过程中请求取消或用户删除
→ 从等待队列移除

容器停止或删除
→ 释放运行容量
→ 通知下一个等待者

容量紧张
→ 尝试停止已达到 idle_stop 条件的用户容器
→ 为新请求腾出运行席位
```

Redis ownership 协调多个 API 和 Worker 进程的 runtime、容量锁、用户变更锁、operation 租约、maintenance、删除标记和最近活动时间。

## 6. 回收空闲容器

```text
后台 cleanup 周期开始
→ 汇总内存、Redis、活动文件和 Docker 时间戳
→ 计算每个用户最近活动时间

空闲达到 idle_stop_seconds
→ 停止容器
→ 保留容器定义和 Named Volume
→ 释放运行容量

空闲达到 idle_remove_seconds
→ 删除容器
→ 保留 Named Volume

cleanup 失败
→ 增加连续失败次数
→ 保存最近错误
→ 暴露 DockerSandboxHealth
```

## 7. 删除对话和用户沙箱

```text
删除对话沙箱
→ 设置 conversation 删除标记
→ 获取用户和会话 maintenance
→ 等待现有 operation 完成
→ 删除 conversation 目录
→ 删除该对话 UID 注册项
→ 清理进程内会话锁

删除用户沙箱
→ 设置 user 删除标记
→ 取消该用户容量等待
→ 获取 user maintenance
→ 等待该用户全部 operation 完成
→ 删除用户容器
→ 删除用户 Named Volume
→ 释放容量并清理 Redis ownership 状态
```

目标已经不存在时按完成处理，使生命周期任务可以安全重试。

## 8. 挂载只读 Agent Skill

```text
应用收集 app/analytics/agents/{agent}/skills
→ 校验源目录和 /skills/{agent} 目标路径
→ 拒绝重复、嵌套或位于 /workspace、/tmp 的目标
→ 以 Docker bind mount mode=ro 挂载
→ 在 Agent CompositeBackend 中路由 Skill 路径

Agent 使用 Skill
→ 可以读取文档和脚本
→ 可以执行 Skill 脚本
→ Docker 和文件中间件拒绝写回 Skill 目录
```

## 资源限制和代码

```text
资源上限
→ max_file_bytes
→ max_workspace_bytes
→ max_file <= max_workspace

工作区配额
→ application 模式由应用计算并强制
→ volume_driver 模式把配额参数交给卷驱动

代码
→ app/sandbox/manager.py
→ app/sandbox/backend.py
→ app/sandbox/archive.py
→ app/sandbox/paths.py
→ app/sandbox/capacity.py
→ app/sandbox/concurrency.py
→ app/sandbox/ownership.py
→ app/sandbox/providers.py
```
