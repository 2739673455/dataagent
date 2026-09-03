# 02. Identity 模块职责与实现

`identity` 负责平台账号认证，以及用户访问 Doris 数据时使用的角色、查询身份和权限。

## 模块职责与边界

`identity` 回答两个核心问题：当前请求代表哪个平台用户，以及这个用户能够以什么 Doris 身份访问哪些数据。模块统一管理平台账号、Token、管理员资格、Doris 角色查询身份、SELECT 资产授权投影和 Row Policy 操作入口。

普通用户通过认证接口登录、刷新会话、退出和修改密码；平台管理员通过管理接口维护用户、Doris 角色和数据权限；`metadata`、`query`、`assistant` 与 `workflows` 通过服务契约读取认证用户、分析资格、查询身份和资产策略。

平台账号和稳定授权投影以认证 PostgreSQL 为事实来源，Doris 角色、真实 SELECT 权限和 Row Policy 以 Doris 为最终执行边界。对话、查询、元数据和沙箱资源仍由所属模块管理。

## 功能清单

```text
Identity
→ 登录和刷新会话
→ 修改密码和退出登录
→ 初始化首个管理员
→ 管理用户
→ 管理 Doris 角色查询身份
→ 为用户绑定 Doris 角色
→ 管理 SELECT 资产权限
→ 管理 Doris 行级策略
→ 发起用户注销
```

## 1. 登录和刷新会话

**实现目的**

建立可撤销、可轮换的用户会话，在每次请求时重新确认账号仍然有效，并限制密码猜测、Refresh Token 重放和长期 Token 泄露带来的风险。

**使用者与使用方式**

- 普通用户和管理员通过 `/api/v1/auth/login` 使用用户名或邮箱登录。
- 客户端在 Access Token 到期前后通过 `/api/v1/auth/refresh` 轮换 Token。
- 所有受保护接口通过 Bearer Access Token 解析当前用户。
- `/api/v1/auth/me` 用于客户端恢复当前账号、管理员标记和 Doris 角色信息。

**具体实现**

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

认证限流使用 Redis 共享计数：登录 IP 每分钟 30 次、登录标识每分钟 10 次、刷新 IP 每分钟 60 次。所有 API Worker 使用同一 `auth.rate_limit_redis_url`；Redis 保存 SHA-256 摘要键、有限容量的活跃桶和自动过期时间，不保存原始 IP 或登录标识。


### 设计细节：密码校验控制 CPU 并发，并隐藏账号是否存在

Argon2id 是 CPU 和内存密集型操作。服务通过进程内 Semaphore 限制同时执行的哈希任务，并把同步密码库放在线程池执行，避免阻塞事件循环。未知账号仍校验一份预生成的 dummy hash，使“账号不存在”和“密码错误”走近似的计算路径。

```python
class Argon2PasswordManager:
    def __init__(self, *, max_concurrency: int = ARGON2_MAX_CONCURRENCY) -> None:
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash = self._password_hash.hash("dataagent-dummy-password")
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def verify(self, password: str, password_hash: str) -> bool:
        async with self._semaphore:
            return await to_thread.run_sync(
                self._password_hash.verify,
                password,
                password_hash,
            )

    async def verify_dummy_password(self, password: str) -> None:
        await self.verify(password, self._dummy_hash)
```

登录标识先执行 `strip().casefold()`，用户名和邮箱在写入时也使用相同规范化规则。数据库唯一约束承担最终并发冲突检查。


### 设计细节：Access Token 每次请求都与用户当前安全版本比较

Access Token 携带 `sub` 和 `auth_version`。认证依赖验证签名、签发者、过期时间和令牌类型后，重新读取用户并比较数据库中的当前版本：

```python
async def authenticate(self, access_token: str) -> AuthenticatedUser:
    claims = self._codec.decode_access_token(access_token)
    user = await self._repo.get_user_by_id(claims.user_id)
    if user is None:
        raise auth_error.InvalidTokenError
    _ensure_active_user(user)
    if user.auth_version != claims.auth_version:
        raise auth_error.InvalidTokenError
    return AuthenticatedUser.from_user(user)
```

修改密码、管理员修改账号安全字段和角色绑定时都会增加 `auth_version`。因此旧 Access Token 即使尚未到 `exp`，下一次请求也会失效。返回值使用脱离数据库 Session 的不可变 `AuthenticatedUser` 快照，路由后续处理不会依赖已关闭的 ORM 对象。


### 设计细节：Refresh Token 使用单次轮换和令牌族重放检测

服务端只保存 Refresh Token 的 SHA-256 摘要。刷新时同时锁定用户和 Token 行，在一个 PostgreSQL 事务中校验 `user_id`、`family_id`、摘要和撤销状态；有效 Token 被标记为已撤销并指向后继 Token。

```python
async with self._repo.session.begin():
    loaded_user = await self._repo.get_user_by_id_for_update(claims.user_id)
    current = await self._repo.get_refresh_token_for_update(claims.token_id)
    if (
        loaded_user is None
        or current is None
        or current.user_id != claims.user_id
        or current.family_id != claims.family_id
        or not hmac.compare_digest(current.token_hash, token_digest)
    ):
        raise auth_error.InvalidTokenError
    if current.revoked_at is not None:
        await self._repo.revoke_refresh_family(current.family_id, now)
        reuse_detected = True
    else:
        replacement_id = uuid4()
        token_pair = await self._issue_token_pair(
            loaded_user,
            current.family_id,
            refresh_token_id=replacement_id,
        )
        self._repo.rotate_refresh_token(current, replacement_id, now)
```

同一个 Token 被两个请求并发使用时，行锁使第二个请求在第一个提交后看到 `revoked_at`，随后撤销整个 family。退出登录也撤销整个 family，客户端不能保留同一登录链上的旧 Token 继续刷新。

## 2. 修改密码和退出登录

**实现目的**

让用户主动终止当前刷新链，并在密码变化后立即废止此前签发的认证状态。

**使用者与使用方式**

- 已登录用户通过 `/api/v1/auth/change-password` 提交旧密码和新密码。
- 客户端通过 `/api/v1/auth/logout` 提交当前 Refresh Token 退出登录。
- 修改密码后，客户端需要使用新密码重新登录。

**具体实现**

```text
用户修改密码
→ 校验旧密码
→ 写入新的 Argon2 密码哈希
→ 增加用户 auth_version
→ 撤销该用户已有 Refresh Token
→ 现有 Access Token 和刷新链全部失效

用户退出登录
→ 撤销当前 Refresh Token 所属的完整 token family
→ 当前刷新链不能继续续期
```

### 设计细节：密码变更与会话失效在同一事务中提交

新密码的 Argon2 计算先在事务外完成，缩短用户行的锁定时间；旧密码复核、密码写入和 Refresh Token 撤销则共享一个数据库事务：

```python
password_hash = await self._password_manager.hash(new_password)

async with self._repo.session.begin():
    user = await self._repo.get_user_by_id_for_update(user_id)
    if user is None:
        raise auth_error.InvalidTokenError
    _ensure_active_user(user)
    if not await self._password_manager.verify(
        current_password,
        user.password_hash,
    ):
        raise auth_error.InvalidCurrentPasswordError
    await self._repo.set_user_password(user, password_hash)
    await self._repo.revoke_user_refresh_tokens(user.id, self._now())
```

`set_user_password()` 同时递增 `auth_version`。因此事务提交后，数据库中的刷新令牌已被撤销，仍在客户端的 Access Token 也会因版本不匹配而失效。退出登录调用 `revoke_refresh_family()`，撤销范围限定为当前设备登录形成的令牌族。

## 3. 初始化首个管理员

**实现目的**

在系统还没有可用管理账号时提供命令行引导入口，并支持部署脚本重复执行而不会创建重复账号或静默接管已有账号。

**使用者与使用方式**

- 部署或开发人员在 `conf/.env` 配置 `ADMIN_USERNAME`、`ADMIN_EMAIL` 和 `ADMIN_PASSWORD`。
- 在项目根目录执行 `uv run -m scripts.bootstrap_admin`。
- 命令会报告账号是新建、已存在还是被提升为管理员。

**具体实现**

```text
启动引导命令
→ 读取并校验显式管理员凭据
→ 计算 Argon2 密码哈希
→ 获取认证安全变更锁
→ 按用户名和邮箱查询现有账号

账号不存在
→ 创建启用状态的管理员
→ 不自动绑定 Doris 角色

同一账号已经存在
→ 要求用户名、邮箱和密码全部匹配
→ 账号未启用时拒绝继续
→ 普通账号可以提升为管理员

用户名或邮箱与不同账号冲突
→ 返回冲突错误
→ 不修改任何已有账号
```

### 设计细节：初始化命令以凭据完全匹配实现幂等

多个实例可能同时执行初始化，因此检查和写入都位于认证安全变更锁内。已有账号只有在用户名、邮箱指向同一记录且密码验证通过时才能复用：

```python
async with self._repo.session.begin():
    await self._repo.lock_security_mutation()
    by_username = await self._repo.get_user_by_username(normalized_username)
    by_email = await self._repo.get_user_by_email(normalized_email)
    existing = by_username or by_email
    if existing is not None:
        if (
            by_username is None
            or by_email is None
            or by_username.id != by_email.id
            or not await self._password_manager.verify(
                password,
                existing.password_hash,
            )
        ):
            raise auth_error.UserAlreadyExistsError(
                detail="初始化账号与现有账号冲突"
            )
        _ensure_active_user(existing)
        if not existing.is_admin:
            await self._repo.update_user(
                existing,
                doris_role=None,
                update_doris_role=False,
                is_admin=True,
            )
```

这项完全匹配检查防止部署配置中的用户名或邮箱误命中现有用户。提升管理员时保留原有 Doris 角色；新建管理员则不自动绑定数据角色，将平台管理权和数据查询权分开配置。

## 4. 管理用户

**实现目的**

集中维护能够登录平台的账号、管理员资格和默认数据角色，并保证用户名、邮箱、最后管理员和认证会话的一致性。

**使用者与使用方式**

- 平台管理员通过 `/api/v1/admin/users` 分页查询或搜索用户。
- 管理员可以创建账号，设置初始密码、管理员标记和 Doris 角色。
- 管理员可以修改用户名、邮箱、密码、管理员标记和 Doris 角色。
- 删除用户通过持久化注销流程完成，直接修改接口不提供任意启用或禁用开关。

**具体实现**

```text
管理员创建用户
→ 校验用户名和邮箱唯一
→ 保存密码哈希
→ 创建为启用状态并设置管理员标记
→ 可选绑定一个 Doris 角色
→ 未指定角色时使用当前默认 Doris 角色

管理员修改用户
→ 修改用户名、邮箱等基础资料
→ 设置或取消管理员身份
→ 更换或解除 Doris 角色绑定
→ 可选重置用户密码
→ 撤销该用户已有 Refresh Token

安全状态发生变化
→ 用户自行修改密码时增加 auth_version 并撤销 Refresh Token
→ 管理员修改用户时增加 auth_version 并撤销 Refresh Token
→ 旧认证状态不能继续使用
```

普通认证要求账号启用；分析接口还要求用户已经绑定可用 Doris 角色；管理接口额外要求 `is_admin=true`。

### 设计细节：用户变更共享安全锁并保护最后一个管理员

创建用户时，在同一安全变更临界区内解析默认角色并检查账号唯一性；更新用户时，角色存在性、最后管理员和唯一性检查也与写入共享该锁：

```python
async with self._repo.session.begin():
    await self._repo.lock_security_mutation()
    if normalized_doris_role is not None:
        identity = await self._repo.get_query_identity(normalized_doris_role)
        if identity is None:
            raise auth_error.RoleNotFoundError

    user = await self._repo.get_user_by_id(user_id)
    if user is None:
        raise auth_error.UserNotFoundError
    if (
        is_admin is not None
        and user.is_admin
        and not is_admin
        and await self._repo.count_admins() <= 1
    ):
        raise auth_error.LastAdministratorError
```

锁把“检查后写入”串行化，避免并发请求同时通过最后管理员检查。管理员修改会撤销目标用户全部 Refresh Token；仓储在安全字段发生变化时递增 `auth_version`，使旧 Access Token 同步失效。

## 5. 管理 Doris 角色查询身份

**实现目的**

为每个业务数据角色建立稳定、只读且可审计的查询身份，使多个平台用户能够共享授权范围和查询经验，同时避免使用 Doris 管理员账号执行分析 SQL。

**使用者与使用方式**

- 管理员查看 Doris 已有角色和可用 Workload Group。
- 管理员创建受管角色，指定角色描述、专用 `query_user` 和 Workload Group。
- 管理员设置或清除新用户使用的默认角色，并删除不再使用的受管角色。
- `query` 在每次执行 SQL 前解析角色专用凭据和授权代次。

**具体实现**

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
→ 按 role_name 复用凭据匹配的 Doris 连接池
→ 使用 query_user 借出查询连接，并把 workload_group 写入查询限制

管理员修改角色
→ 修改描述或 workload_group
→ 切换全局唯一默认角色

管理员删除角色
→ 校验并处理用户绑定和授权
→ 快照查询身份、SELECT 权限和 Row Policy
→ 删除 Doris 查询用户和角色关系
→ 删除 PostgreSQL 查询身份与权限投影
→ Doris 与 PostgreSQL 任一侧失败时按完成步骤补偿
```

应用启动时会检查每个 `query_user` 只拥有预期角色、只能访问配置的数据范围，并且没有写权限。检查失败会记录警告并允许应用继续启动，管理员可通过应用修复配置；实际查询仍由身份解析和 Doris 权限边界拒绝。

### 设计细节：查询身份在 Doris 和 PostgreSQL 之间使用补偿事务

角色及查询用户必须先在 Doris 建立，随后才能把加密凭据保存为平台投影。PostgreSQL 写入失败时，异常路径删除刚创建的 Doris 对象：

```python
password = self._cipher.generate_password()
doris_created = False
try:
    async with self._repo.session.begin():
        await self._repo.lock_security_mutation()
        await self._doris_repo.create_role_identity(
            role_name=role,
            query_user=query_user,
            password=password,
            workload_group=workload_group,
        )
        doris_created = True
        return await self._repo.add_query_identity(
            DorisQueryIdentity(
                role_name=role,
                query_user=query_user,
                encrypted_password=self._cipher.encrypt(password),
                workload_group=workload_group,
                is_default=False,
            )
        )
except BaseException:
    if doris_created:
        await self._doris_repo.drop_role_identity(
            role_name=role,
            query_user=query_user,
        )
    raise
```

明文密码只存在于创建过程和执行前的内存中，持久化层保存密文。捕获 `BaseException` 让协程取消也触发补偿；补偿本身失败时会记录高优先级日志，由管理员核对 Doris 实态。

## 6. 为用户绑定 Doris 角色

**实现目的**

把平台身份映射到确定的数据访问身份，使认证、元数据召回、SQL Guard、Doris 执行和查询经验召回使用同一个角色边界。

**使用者与使用方式**

- 管理员在创建或修改用户时选择一个受管 Doris 角色。
- 管理员通过将 `doris_role` 明确设置为空解除绑定。
- `metadata` 和 `query` 根据当前用户的绑定角色获取资产策略。
- `assistant` 在开始分析前检查用户是否具备可用角色。

**具体实现**

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

### 设计细节：执行身份只从当前用户绑定即时解析

Query 不接收上层传入的 Doris 用户名或密码，而是用平台用户 ID 在执行前解析唯一查询身份：

```python
user = await self._repo.get_user_by_id(user_id)
if user is None:
    raise auth_error.UserNotFoundError
if not user.is_active:
    raise auth_error.InactiveUserError
if user.doris_role_name is None:
    raise QueryPrincipalNotConfiguredError("用户尚未配置 Doris 角色")

identity = await self._repo.get_query_identity(user.doris_role_name)
if identity is None:
    raise QueryPrincipalNotConfiguredError(
        "用户的 Doris 角色尚未配置可用的查询身份"
    )
return ResolvedQueryPrincipal(
    role_name=user.doris_role_name,
    authorization_epoch=identity.authorization_epoch,
    query_user=identity.query_user,
    password=self._cipher.decrypt(identity.encrypted_password),
    workload_group=identity.workload_group,
)
```

返回值把角色、授权代次、专用账号和 Workload Group 固化为一次执行快照。调用方只能使用解析结果建立连接，避免客户端绕过用户绑定选择更高权限身份。

## 7. 管理 SELECT 资产权限

**实现目的**

支持数据库、表和字段粒度的数据隔离，并让应用在访问 Doris 前完成目录过滤和可解释的权限拒绝。

**使用者与使用方式**

- 管理员按 Doris 角色查看、授予、回收或全部回收 SELECT 权限。
- 表名为空表示数据库级授权；指定表且字段为空表示表级授权；同时指定表和字段表示列级授权。
- `metadata` 使用 `AssetAccessPolicy` 过滤召回结果。
- `query` 使用同一策略校验 SQL 实际读取的表和字段，Doris 再执行最终权限检查。

**具体实现**

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


### 设计细节：资产授权用层级包含关系统一回答“可访问”和“可见”

一个授权对象按数据源、数据库、表、字段逐级收窄。上层字段为 `None` 表示授权覆盖其全部下级资产。`allows()` 用于确认完整访问权；`is_visible()` 还允许父目录在存在任一下级授权时出现在目录中。

```python
def encompasses(self, other: "AssetIdentity") -> bool:
    own_parts = (
        self.data_source,
        self.database_name,
        self.table_name,
        self.column_name,
    )
    other_parts = (
        other.data_source,
        other.database_name,
        other.table_name,
        other.column_name,
    )
    return all(
        own is None or own == target
        for own, target in zip(own_parts, other_parts, strict=True)
    )


def allows(self, asset: AssetIdentity) -> bool:
    return any(grant.encompasses(asset) for grant in self.grants)

def is_visible(self, asset: AssetIdentity) -> bool:
    return self.allows(asset) or any(
        asset.encompasses(grant) for grant in self.grants
    )
```

例如只有 `orders.amount` 字段权限时，`orders` 表可以出现在元数据目录中，但查询 `orders.*` 不会通过 Guard。Metadata 和 Query 共用这一策略快照，避免目录展示与 SQL 校验采用两套权限规则。


### 设计细节：Doris 权限和 PostgreSQL 投影通过安全锁与补偿保持收敛

Doris 不能加入 PostgreSQL 事务。所有账号、角色和权限安全变更先取得同一把 PostgreSQL advisory transaction lock，避免两个请求交错修改真实权限和应用投影。授予 SELECT 时先改 Doris，再写投影；投影写入或事务提交失败时执行反向 REVOKE：

```python
try:
    async with self._repo.session.begin():
        await self._repo.lock_security_mutation()
        await self._require_role_exists(role)
        await self._doris_repo.grant_select(
            role_name=role,
            catalog=self._catalog,
            database=self._database,
            table=table_name,
            columns=granted_columns,
        )
        doris_changed = True
        result: list[DorisRoleAssetGrant] = []
        for asset, current_grant in zip(assets, existing, strict=True):
            persisted_grant = current_grant
            if persisted_grant is None:
                persisted_grant = await self._repo.add_asset_grant(
                    DorisRoleAssetGrant(
                        role_name=role,
                        scope=asset.scope.value,
                        data_source=asset.data_source,
                        database_name=asset.database_name,
                        table_name=asset.table_name,
                        column_name=asset.column_name,
                        resource_key=asset.resource_key,
                    )
                )
            result.append(persisted_grant)
except BaseException:
    if doris_changed:
        await self._compensate_select(
            grant=False,
            role_name=role,
            table_name=table_name,
            columns=granted_columns,
        )
    raise
```

捕获 `BaseException` 是为了让任务取消也进入补偿路径。角色删除会先快照查询用户、密码、Workload Group、SELECT 授权和 Row Policy，再根据 Doris 已完成步骤恢复。补偿失败会记录高优先级错误，管理员需要处理真实权限与投影可能暂时分离的情况。

## 8. 管理 Doris 行级策略

**实现目的**

在表和字段授权之上限制角色能够看到的具体数据行，并让行策略变化立即形成新的授权环境。

**使用者与使用方式**

- 管理员按角色查看 Doris 当前 Row Policy。
- 管理员指定策略名、目标表和谓词创建行策略。
- 管理员按策略名删除行策略。
- `query` 无需重写 SQL，Doris 在执行阶段自动应用角色的 Row Policy。

**具体实现**

```text
管理员查看角色行策略
→ 向 Doris 执行 SHOW ROW POLICY
→ 返回 Doris 当前实时策略

管理员创建行策略
→ 校验角色和 predicate 为单个 SQL 表达式
→ 交由 Doris 校验目标表、字段和表达式返回类型
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

### 设计细节：谓词校验、Doris 写入和授权代次轮换形成一个用例

服务先把谓词限制为单个 SQL 表达式，再在安全锁内创建 Doris 策略并轮换授权代次。数据库事务未提交时，会删除刚创建的策略：

```python
predicate_sql = self._validate_predicate(predicate)
doris_changed = False
try:
    async with self._repo.session.begin():
        await self._repo.lock_security_mutation()
        await self._require_role_exists(role)
        await self._doris_repo.create_row_policy(
            policy_name=policy_name,
            role_name=role,
            catalog=self._catalog,
            database=self._database,
            table=table_name,
            policy_type=policy_type,
            predicate_sql=predicate_sql,
        )
        doris_changed = True
        await self._rotate_authorization_epoch(role)
except BaseException:
    if doris_changed:
        await self._doris_repo.drop_row_policy(
            policy_name=policy_name,
            role_name=role,
            catalog=self._catalog,
            database=self._database,
            table=table_name,
        )
    raise
```

表达式的字段存在性和布尔返回类型最终由 Doris 校验。应用侧语法检查负责阻止多语句和超出谓词边界的结构；授权代次轮换负责让旧查询经验立即退出可召回范围。


### 设计细节：authorization_epoch 是角色权限环境的代次

回收 SELECT 权限、创建或删除 Row Policy 时，查询身份会生成新的 `authorization_epoch`：

```python
async def _rotate_authorization_epoch(self, role_name: str) -> None:
    identity = await self._repo.get_query_identity(role_name)
    if identity is None:
        raise auth_error.RoleNotFoundError
    identity.rotate_authorization_epoch()
    await self._repo.flush()
```

Query 解析一次执行身份时同时读取该代次。查询经验和会话内的经验缓存都记录它；角色权限发生收窄后，旧代次经验不会继续召回。单纯新增 SELECT 权限不会使既有经验变得越权，因此授予路径不轮换代次。

## 9. 发起用户注销

**实现目的**

在立即阻止目标用户继续访问系统的同时，可靠清理分布在认证库、Agent Checkpoint、语义召回快照和 Docker Volume 中的全部用户资源。

**使用者与使用方式**

- 平台管理员通过删除用户接口发起注销。
- 接口只负责受理、禁用账号和创建持久任务，调用方无需等待资源清理完成。
- `workflows`、Celery Worker 和 Beat 负责执行、重试与恢复后续清理。

**具体实现**

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


### 设计细节：注销受理在一个认证事务中完成即时封禁

注销请求必须先形成可靠的本地事实，再由 Workflows 清理外部资源。禁用账号、吊销 Refresh Token 和创建 `UserDeletionTask` 使用同一事务，并在安全锁内保护最后管理员规则：

```python
async with self._postgres.session() as session:
    repo = IdentityPGRepo(session)
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

事务提交后用户立即无法通过 Access Token 或 Refresh Token 继续访问。外部清理失败只会延迟物理删除，不会重新启用账号。

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

命令行
→ scripts/bootstrap_admin.py：初始化或确认首个管理员
```
