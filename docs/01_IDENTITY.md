# Identity 模块功能

`identity` 负责平台账号认证，以及用户访问 Doris 数据时使用的角色、查询身份和权限。

## 功能清单

```text
Identity
→ 登录和刷新会话
→ 修改密码和退出登录
→ 管理用户
→ 管理 Doris 角色查询身份
→ 为用户绑定 Doris 角色
→ 管理 SELECT 资产权限
→ 管理 Doris 行级策略
→ 发起用户注销
```

## 1. 登录和刷新会话

```text
用户提交用户名/邮箱和密码
→ 按客户端 IP 和登录标识限流
→ 校验用户存在、账号启用和 Argon2 密码哈希
→ 签发短期 Access Token
→ 签发随机 Refresh Token
→ 只保存 Refresh Token 哈希及其 family_id
→ 返回两个 Token

客户端使用 Access Token 请求接口
→ 解析 Token 的用户 ID、过期时间和 auth_version
→ 重新读取当前用户
→ 校验账号仍启用
→ 校验 Token auth_version 等于用户当前 auth_version
→ 建立当前用户身份

客户端使用 Refresh Token 续期
→ 按客户端 IP 限流
→ 校验 Token 哈希、过期时间和撤销状态
→ 撤销本次使用的旧 Token
→ 签发并关联一个后继 Token
→ 返回新的 Access Token 和 Refresh Token

已经撤销的 Refresh Token 再次出现
→ 判定为 Token 重放
→ 撤销同一 family 的全部 Refresh Token
→ 要求用户重新登录
```

默认进程内限流为：登录 IP 每分钟 30 次、登录标识每分钟 10 次、刷新 IP 每分钟 60 次。

## 2. 修改密码和退出登录

```text
用户修改密码
→ 校验旧密码
→ 写入新的 Argon2 密码哈希
→ 增加用户 auth_version
→ 撤销该用户已有 Refresh Token
→ 现有 Access Token 和刷新链全部失效

用户退出登录
→ 撤销当前 Refresh Token
→ 当前刷新链不能继续续期
```

## 3. 管理用户

```text
管理员创建用户
→ 校验用户名和邮箱唯一
→ 保存密码哈希
→ 设置启用状态和管理员标记
→ 可选绑定一个 Doris 角色

管理员修改用户
→ 修改用户名、邮箱等基础资料
→ 启用或禁用账号
→ 设置或取消管理员身份
→ 更换或解除 Doris 角色绑定

安全状态发生变化
→ 增加 auth_version 或撤销 Refresh Token
→ 旧认证状态不能继续使用
```

普通认证要求账号启用；分析接口还要求用户已经绑定可用 Doris 角色；管理接口额外要求 `is_admin=true`。

## 4. 管理 Doris 角色查询身份

```text
管理员创建受管 Doris 角色
→ 创建或确认 Doris 角色
→ 创建该角色专用的 query_user
→ 生成随机查询密码
→ 只把 query_user 绑定到该角色
→ 加密查询密码
→ 保存 role_name、query_user、workload_group 和 authorization_epoch
→ 可选设置为默认角色

查询模块需要执行 SQL
→ 按用户绑定的 role_name 读取查询身份
→ 在建立连接前解密查询密码
→ 使用 query_user 和 workload_group 创建短生命周期连接

管理员修改角色
→ 修改描述或 workload_group
→ 切换全局唯一默认角色

管理员删除角色
→ 校验并处理用户绑定和授权
→ 删除 Doris 查询用户和角色关系
→ 删除 PostgreSQL 查询身份
→ Doris 与 PostgreSQL 任一侧失败时执行补偿
```

应用启动时会检查每个 `query_user` 只拥有预期角色、只能访问配置的数据范围，并且没有写权限。

## 5. 为用户绑定 Doris 角色

```text
管理员选择平台用户和 Doris 角色
→ 校验用户存在
→ 校验角色具有受管查询身份
→ 更新 User.doris_role_name
→ 用户后续查询和召回使用该角色的权限

管理员解除角色绑定
→ 清空 User.doris_role_name
→ 用户仍可登录
→ 用户不能调用分析查询能力
```

一个用户最多绑定一个角色；多个用户可以共享同一角色的查询身份和查询经验。

## 6. 管理 SELECT 资产权限

```text
管理员为角色授予资产
→ 选择 data_source、database、table 或 column 层级
→ 在 Doris 执行 GRANT
→ 在 PostgreSQL 写入 DorisRoleAssetGrant 投影
→ 生成稳定 resource_key
→ 任一侧失败时补偿另一侧

管理员回收资产
→ 在 Doris 执行 REVOKE
→ 删除 PostgreSQL 权限投影
→ 轮换角色 authorization_epoch

上层模块读取用户授权
→ 校验用户和绑定角色
→ 读取角色当前 authorization_epoch
→ 加载角色全部授权投影
→ 构造 AssetAccessPolicy
→ metadata 用它过滤召回目录
→ query 用它校验 SQL 实际资产
```

应用侧策略负责提前过滤和返回可解释错误，Doris 权限负责最终访问隔离。

## 7. 管理 Doris 行级策略

```text
管理员查看角色行策略
→ 向 Doris 执行 SHOW ROW POLICY
→ 返回 Doris 当前实时策略

管理员创建行策略
→ 校验角色和目标表
→ 解析并校验 predicate
→ 在 Doris 执行 CREATE ROW POLICY
→ 轮换角色 authorization_epoch
→ Doris 失败或数据库提交失败时执行补偿

管理员删除行策略
→ 读取原策略用于补偿
→ 在 Doris 执行 DROP ROW POLICY
→ 轮换角色 authorization_epoch
→ 后续失败时尝试恢复原策略
```

行策略只存储在 Doris。PostgreSQL 通过查询身份的 `authorization_epoch` 标识当前授权代次。

## 8. 发起用户注销

```text
管理员请求注销用户
→ 拒绝注销当前操作管理员
→ 拒绝注销唯一启用的管理员
→ 禁用目标用户
→ 撤销目标用户 Refresh Token
→ 创建或复用 UserDeletionTask
→ 提交跨模块用户注销任务

注销任务执行完成
→ 删除 User 记录
→ 将 UserDeletionTask 标记为 completed

注销任务执行失败
→ 保存失败原因、尝试次数和下次执行时间
→ 周期任务重新提交
```

对话、LangGraph 和沙箱资源的实际清理由 `workflows` 编排。

## 数据与接口

```text
认证 PostgreSQL
→ User
→ RefreshToken
→ DorisQueryIdentity
→ DorisRoleAssetGrant
→ UserDeletionTask

Doris
→ 角色和 query_user
→ SELECT 权限
→ Row Policy

/api/v1/auth
→ login、refresh、logout、change-password、me

/api/v1/admin
→ 用户、角色、SELECT 权限、Row Policy 和用户注销管理
```

代码位于 `app/identity/api`、`models`、`repositories` 和 `services`。
