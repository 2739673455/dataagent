# Auth Service

基于 FastAPI 的认证服务后端，提供用户、角色、权限管理等功能。

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 填写配置信息

[configs/.env](configs/.env) 环境变量  
[configs/config.yml](configs/config.yml) 应用配置  

### 3. 初始化数据库

```bash
uv run app/init_db.py
```

### 4. 启动服务

```bash
uv run -m app.main
```

## 认证流程

### 1. 访问应用

用户访问 `http://app.com/`，应用检查前端 localStorage 中是否存在访问令牌：
- 如果无访问令牌，应用发起授权请求。
- 如果有访问令牌，应用前端携带令牌请求应用后端 `app/api/userinfo` 获取用户信息。
  - 应用后端先请求认证后端 `auth/api/introspection` 检查访问令牌是否有效。
  - 如果令牌无效，应用后端返回 401，前端清理 localStorage 中的访问令牌，并发起授权请求。
  - 如果令牌有效，应用后端根据令牌获取用户信息，返回给前端，流程结束。

### 2. 应用生成授权请求

应用前端生成下列授权参数：
- `response_type` = code
- `client_id` = app
- `redirect_uri` = http://app.com/auth/callback
- `state` = <random-state>
- `code_challenge` = <pkce-code-challenge>
- `code_challenge_method` = S256

其中：
- `redirect_uri` 是认证中心完成授权后固定跳转的应用回调页。
- `state` 用于防止 CSRF (跨站请求伪造)，并关联登录前的目标页面，例如 `http://app.com/`。
- `code_challenge` 用于 PKCE。

生成方式：
- `state` 使用 CSPRNG 生成 32 字节随机数，再做 Base64URL 编码，并去掉 `=` padding。
- `code_verifier` 使用 CSPRNG 生成 32 字节随机数，再做 Base64URL 编码，并去掉 `=` padding。
- `code_challenge` 由 `code_verifier` 计算得到：
  - `code_challenge = BASE64URL(SHA256(code_verifier))`
  - `code_challenge_method = S256`
- 授权请求中只发送 `code_challenge`，不发送 `code_verifier`。
- 回调后应用使用授权码换取访问令牌时，再提交 `code_verifier`。认证中心重新计算 `code_challenge` 并与授权码绑定的 `code_challenge` 比对，匹配后才签发令牌。

应用在 sessionStorage 中临时保存以下信息供回调阶段校验使用：
- `state`
- `code_verifier`
- 登录完成后的最终返回地址 `return_to`

之后携带参数跳转到认证中心授权页：`http://auth.com/authorize?response_type=code&client_id=app&redirect_uri=http://app.com/auth/callback&state=...&code_challenge=...&code_challenge_method=S256`

### 3. 认证中心检查登录态

认证中心收到授权请求后，先检查 `session_id` Cookie：
- 如果已有登录态，继续授权流程，校验授权参数。
- 如果没有登录态，跳转到认证中心登录页，并原样透传授权参数。

登录与注册：
- 登录页、注册页、忘记密码页之间切换时，都原样透传授权参数  
- 忘记密码页设置完新密码后跳转注册页  
- 登录页、注册页、忘记密码页只负责用户认证相关操作

登录或注册成功后：
- 认证中心后端保存 session 记录：
  - `session_id`
  - `user_id`
  - `created_at`
  - `expires_at`
  - `revoked_at`
- 认证中心写入 `session_id` Cookie
- 固定跳回认证中心授权页 `http://auth.com/authorize`，同时原样携带授权参数。

### 4. 认证中心校验授权参数

认证中心校验授权参数：
- `response_type`：必须存在，且值必须为 `code`。
- `client_id`：必须存在，且应用必须存在于认证中心的客户端注册表中，并且处于启用状态。
- `redirect_uri`：必须存在，且必须和该 `client_id` 在客户端注册表中配置的回调地址精确匹配，例如 `http://app.com/auth/callback`。
- `state`：必须存在，必须是 Base64URL 字符串，长度必须为 43 个字符。认证中心不解析 `state` 内容，只保存并在回调时原样返回。
- `code_challenge`：必须存在，必须是 Base64URL 字符串，长度必须为 43 个字符。认证中心不解析 `code_challenge` 内容，但需要将其与授权请求绑定保存，供 token 阶段校验 `code_verifier` 使用。
- `code_challenge_method`：必须存在，且值必须为 `S256`。

如果授权参数校验失败，认证中心统一显示授权错误页面，展示通用提示：`授权请求无效，请返回应用重新发起登录。`

### 5. 认证中心生成授权码

- 授权参数校验成功后，认证中心生成一次性、短有效期、高熵随机授权码 `code`。  
- `code` 使用 CSPRNG 生成 32 字节随机数，再做 Base64URL 编码，并去掉 `=` padding。
- `code` 有效期建议不超过 5 分钟，且只能使用一次。
- 认证中心保存授权码记录：
  - `code`
  - `user_id`
  - `session_id`
  - `client_id`
  - `redirect_uri`
  - `state`
  - `code_challenge`
  - `code_challenge_method`
  - `created_at`
  - `expires_at`
  - `used`=false
- 认证中心跳转到 `redirect_uri`，并携带 `code` 和原样返回的 `state`：`http://app.com/auth/callback?code=...&state=...`

### 6. 应用回调页校验 `state`，并使用授权码换取访问令牌

应用回调页处理流程：
- 从 URL 查询参数中读取 `code` 和 `state`。
- 从当前标签页的 `sessionStorage` 中读取：
  - `state`
  - `code_verifier`
  - `return_to`
- 校验回调参数：
  - `code` 必须存在。
  - `state` 必须存在。
  - sessionStorage 中必须存在 `state` 和 `code_verifier`。
  - URL 中的 `state` 必须和 sessionStorage 中的 `state` 完全一致。

校验回调参数失败后：
- 清理 sessionStorage 中的临时数据。
- 清理 localStorage 中可能存在的旧访问令牌。
- 展示认证失败提示，并允许用户重新发起登录。

校验成功后，应用回调页以 `application/x-www-form-urlencoded` 方式请求访问令牌：
```text
POST http://auth.com/api/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
code=<authorization-code>
client_id=app
redirect_uri=http://app.com/auth/callback
code_verifier=<oidc_code_verifier>
```

认证中心 token 接口校验：
- `grant_type` 必须为 `authorization_code`。
- `code` 必须存在、未过期、未使用。
- `client_id` 必须和授权码记录中的 client_id 一致。
- `redirect_uri` 必须和授权码记录中的 redirect_uri 一致。
- `code_verifier` 必须存在，且计算得到的 `BASE64URL(SHA256(code_verifier))` 必须和授权码记录中的 `code_challenge` 一致。

如果 token 接口校验失败，应用回调页清理临时数据和旧访问令牌，并展示认证失败提示。  

如果 token 接口校验成功：
- 认证中心将授权码标记为已使用。
- 认证中心保存 token 记录
  - `access_token`
  - `user_id`
  - `session_id`
  - `client_id`
  - `created_at`
  - `expires_at`
  - `revoked_at`
  - `active`
  - `permissions`
- 认证中心签发访问令牌，并返回给应用回调页。
- 应用回调页将访问令牌写入前端 localStorage。
- 应用回调页清理 sessionStorage 中的临时数据。
- 应用回调页跳转到 `return_to` 对应的最终返回地址；如果不存在，则跳转到 `http://app.com/`。

### 7. 应用请求业务接口

前端请求应用后端业务接口时，统一携带访问令牌：

```text
Authorization: Bearer <access_token>
```

应用后端通过认证中间件统一处理：
- 从 `Authorization` 请求头中读取 Bearer Token。
- 请求认证中心 `auth/api/introspection` 校验访问令牌。
- 校验令牌是否存在、是否过期、是否已撤销。
- 令牌有效后，根据令牌对应的用户获取身份信息和权限信息。
- 根据当前接口所需权限进行鉴权。

处理结果：
- 令牌有效且权限满足要求：继续处理业务请求。
- 令牌不存在、过期或已撤销：返回 `401`，前端清理 localStorage 中的访问令牌，并重新发起授权请求。
- 令牌有效但权限不足：返回 `403`，前端展示无权限提示。

### 8. 退出登录

应用退出：
- 应用前端读取 localStorage 中的访问令牌，请求认证中心令牌撤销接口。
- 认证中心后端根据访问令牌将对应 token 标记为无效。
- 应用前端清理 localStorage 中的访问令牌。
- 应用前端跳转到应用未登录状态或重新发起授权请求。
- 用户下次进入应用时，如果认证中心仍有登录态，无需输入账号密码，直接重新完成授权流程。

认证中心退出：
- 认证中心前端请求认证中心退出接口，撤销该会话的所有访问令牌。
- 认证中心后端清理当前浏览器在 `auth.com` 下的 `session_id` Cookie。
- 认证中心后端将该 `session_id` 关联的所有访问令牌标记为无效。
- 各应用前端 localStorage 中可能仍保留旧访问令牌，但后续业务请求经过应用后端认证中间件和校验时，会被识别为无效。
- 用户下次进入应用时，需要重新登录认证中心。

## 数据库表定义

#### user

用户表，保存系统账号信息。

| 字段            | 类型         | 可空 | 说明                                 |
| --------------- | ------------ | ---- | ------------------------------------ |
| `id`            | BIGINT       | 否   | 用户 ID，主键                        |
| `email`         | VARCHAR(255) | 否   | 邮箱，唯一                           |
| `name`          | VARCHAR(100) | 否   | 用户名                               |
| `password_hash` | VARCHAR(255) | 否   | 密码哈希                             |
| `yn`            | TINYINT      | 否   | 是否启用，`1` 表示启用，`0` 表示禁用 |
| `created_at`    | DATETIME     | 否   | 创建时间                             |
| `updated_at`    | DATETIME     | 否   | 更新时间                             |

#### role

角色表，用来承载一组权限。

| 字段         | 类型         | 可空 | 说明                                 |
| ------------ | ------------ | ---- | ------------------------------------ |
| `id`         | BIGINT       | 否   | 角色 ID，主键                        |
| `name`       | VARCHAR(100) | 否   | 角色名称，唯一                       |
| `yn`         | TINYINT      | 否   | 是否启用，`1` 表示启用，`0` 表示禁用 |
| `created_at` | DATETIME     | 否   | 创建时间                             |
| `updated_at` | DATETIME     | 否   | 更新时间                             |

#### permission

权限表，表示一个具体的操作权限或访问范围。

| 字段          | 类型         | 可空 | 说明                                 |
| ------------- | ------------ | ---- | ------------------------------------ |
| `id`          | BIGINT       | 否   | 权限 ID，主键                        |
| `name`        | VARCHAR(100) | 否   | 权限名称，唯一                       |
| `description` | VARCHAR(255) | 是   | 权限说明                             |
| `yn`          | TINYINT      | 否   | 是否启用，`1` 表示启用，`0` 表示禁用 |
| `created_at`  | DATETIME     | 否   | 创建时间                             |
| `updated_at`  | DATETIME     | 否   | 更新时间                             |

#### user_role_rel

用户与角色关系表。

| 字段      | 类型   | 可空 | 说明                    |
| --------- | ------ | ---- | ----------------------- |
| `user_id` | BIGINT | 否   | 用户 ID，联合主键，外键 |
| `role_id` | BIGINT | 否   | 角色 ID，联合主键，外键 |

#### role_permission_rel

角色与权限关系表。

| 字段            | 类型   | 可空 | 说明                    |
| --------------- | ------ | ---- | ----------------------- |
| `role_id`       | BIGINT | 否   | 角色 ID，联合主键，外键 |
| `permission_id` | BIGINT | 否   | 权限 ID，联合主键，外键 |
