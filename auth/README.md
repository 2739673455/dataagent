# Auth Service
基于 FastAPI 的认证服务后端，提供用户、角色、权限管理等功能。

## 快速开始
填写配置信息：  
[configs/.env](configs/.env) 环境变量  
[configs/config.yml](configs/config.yml) 应用配置  

```bash
uv sync             # 安装依赖
uv run -m app.main  # 启动服务
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
- `state` 用于防止 CSRF (跨站请求伪造)，并校验认证回调是否属于当前标签页发起的授权请求。
- `return_to` 用于保存登录完成后的最终返回地址，例如 `http://app.com/`。
- `code_challenge` 用于 PKCE (防止授权码 `code` 被截获后直接换取访问令牌)，应用只在授权请求中发送由 `code_verifier` 计算出的 `code_challenge`，换取令牌时再提交本地保存的 `code_verifier`，认证中心比对通过后才签发令牌。

生成方式：
- `state` 使用 CSPRNG 生成 32 字节随机数，再做 Base64URL 编码，并去掉 `=` padding。
- `code_verifier` 使用 CSPRNG 生成 32 字节随机数，再做 Base64URL 编码，并去掉 `=` padding。
- `code_challenge` 由 `code_verifier` 计算得到：
  - `code_challenge = BASE64URL(SHA256(code_verifier))`
  - `code_challenge_method = S256`
- 授权请求中只发送 `code_challenge`，不发送 `code_verifier`。
- 回调后应用使用授权码换取访问令牌时，再提交 `code_verifier`。认证中心重新计算 `code_challenge` 并与授权码绑定的 `code_challenge` 比对，匹配后才签发令牌。

应用在 sessionStorage 中临时保存授权请求信息供回调阶段校验使用，key 格式为 `auth-request:{state}`，value 为 JSON 对象，包含以下字段：
- `clientId`
- `redirectUri`
- `returnTo`
- `state`
- `codeVerifier`

之后携带参数跳转到认证中心授权页：`http://auth.com/authorize?response_type=code&client_id=app&redirect_uri=http://app.com/auth/callback&state=...&code_challenge=...&code_challenge_method=S256`

### 3. 认证中心检查登录态
认证中心收到授权请求后，先检查 `session_id` Cookie：
- 如果已有登录态，继续授权流程，校验授权参数。
- 如果没有登录态，跳转到认证中心登录页，并原样透传授权参数。

登录与注册：
- 登录页、注册页、忘记密码页之间切换时，都原样透传授权参数  
- 忘记密码页设置完新密码后跳转登录页  
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
  - `used_at`
- 认证中心跳转到 `redirect_uri`，并携带 `code` 和原样返回的 `state`：`http://app.com/auth/callback?code=...&state=...`

### 6. 应用回调页校验 `state`，并使用授权码换取访问令牌
应用回调页处理流程：
- 从 URL 查询参数中读取 `code` 和 `state`。
- 从当前标签页的 `sessionStorage` 中按 key `auth-request:{state}` 读取 JSON 对象，其中包含 `clientId`、`redirectUri`、`returnTo`、`state`、`codeVerifier`。
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
  - `scope`
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
### 用户，角色，权限表
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

### 认证表
#### auth_session
认证中心登录态表，保存 `auth.com` 下 `session_id` Cookie 对应的服务端会话。
| 字段         | 类型         | 可空 | 说明                         |
| ------------ | ------------ | ---- | ---------------------------- |
| `session_id` | VARCHAR(100) | 否   | 会话 ID，主键                |
| `user_id`    | BIGINT       | 否   | 用户 ID，外键                |
| `created_at` | DATETIME     | 否   | 创建时间                     |
| `expires_at` | DATETIME     | 否   | 过期时间                     |
| `revoked_at` | DATETIME     | 是   | 撤销时间，为空表示未主动撤销 |

#### authorization_code
授权码表，保存一次性授权码及其绑定的授权参数。
| 字段                    | 类型         | 可空 | 说明                             |
| ----------------------- | ------------ | ---- | -------------------------------- |
| `code`                  | VARCHAR(100) | 否   | 授权码，主键                     |
| `user_id`               | BIGINT       | 否   | 用户 ID，外键                    |
| `session_id`            | VARCHAR(100) | 否   | 认证中心会话 ID，外键            |
| `client_id`             | VARCHAR(100) | 否   | 客户端标识                       |
| `redirect_uri`          | VARCHAR(500) | 否   | 本次授权请求使用的回调地址       |
| `state`                 | VARCHAR(100) | 否   | 应用传入的 state，回调时原样返回 |
| `code_challenge`        | VARCHAR(100) | 否   | PKCE code_challenge              |
| `code_challenge_method` | VARCHAR(20)  | 否   | 固定为 `S256`                    |
| `created_at`            | DATETIME     | 否   | 创建时间                         |
| `expires_at`            | DATETIME     | 否   | 过期时间                         |
| `used_at`               | DATETIME     | 是   | 使用时间，为空表示未使用         |

#### access_token
访问令牌表，保存已签发 token 的服务端状态，用于 introspection、撤销和权限实时生效。
| 字段           | 类型         | 可空 | 说明                         |
| -------------- | ------------ | ---- | ---------------------------- |
| `access_token` | VARCHAR(255) | 否   | 访问令牌，主键               |
| `user_id`      | BIGINT       | 否   | 用户 ID，外键                |
| `session_id`   | VARCHAR(100) | 否   | 认证中心会话 ID，外键        |
| `client_id`    | VARCHAR(100) | 否   | 客户端标识                   |
| `scope`        | VARCHAR(255) | 是   | 授权范围                     |
| `created_at`   | DATETIME     | 否   | 创建时间                     |
| `expires_at`   | DATETIME     | 否   | 过期时间                     |
| `revoked_at`   | DATETIME     | 是   | 撤销时间，为空表示未主动撤销 |

#### email_code
邮箱验证码表，保存注册、修改邮箱、重置密码等流程使用的邮箱验证码。
| 字段         | 类型         | 可空 | 说明                     |
| ------------ | ------------ | ---- | ------------------------ |
| `email`      | VARCHAR(255) | 否   | 邮箱，联合主键           |
| `code_type`  | VARCHAR(50)  | 否   | 验证码类型，联合主键     |
| `code`       | VARCHAR(20)  | 否   | 验证码                   |
| `created_at` | DATETIME     | 否   | 创建时间                 |
| `expires_at` | DATETIME     | 否   | 过期时间                 |
| `used_at`    | DATETIME     | 是   | 使用时间，为空表示未使用 |

## 后端接口定义
### 认证接口
#### GET `/api/authorize`
认证中心授权入口。
- 接收 `response_type`、`client_id`、`redirect_uri`、`state`、`code_challenge`、`code_challenge_method`，读取 `session_id` Cookie。
- 检查登录态 `session_id` Cookie。
  - 如果无登录态，跳转登录页，并原样透传授权参数。
  - 如果有登录态，校验授权参数。
    - 如果校验通过，获取会话信息，生成并记录授权码，跳转到 `redirect_uri?code=...&state=...`。
    - 如果校验失败，显示授权错误页面。

#### POST `/api/token`
授权码换访问令牌。
- 以表单接收 `grant_type=authorization_code`、`code`、`client_id`、`redirect_uri`、`code_verifier`。
- 校验授权码未过期、未使用，校验 `client_id`、`redirect_uri` 和 PKCE。
  - 如果校验通过，标记授权码已使用，并签发访问令牌。
  - 如果校验失败，返回 `400` 错误响应；应用回调页收到错误后清理临时数据和旧访问令牌，并展示认证失败提示。

#### POST `/api/introspection`
校验访问令牌。
- 通过 `Authorization: Bearer <access_token>` 传入 token。
- 校验 token 是否存在、是否过期、是否已撤销，并获取当前用户身份和权限。
  - 如果 token 有效，返回 `active=true`、`sub`、`exp`、`scope`。
  - 如果 token 无效，返回 `active=false`。

#### POST `/api/login`
提交登录。
- 请求体包含 `email`、`password`。
- 校验账号是否存在、账号是否启用、密码是否正确。
  - 如果校验成功，创建 session 记录，并通过响应写入 `session_id` Cookie；前端负责跳回 `/api/authorize` 并原样携带授权参数。
  - 如果校验失败。
    - 如果邮箱不存在或密码错误，返回 `400 invalid-credentials`。
    - 如果账号被禁用，返回 `403 user-disabled`。

#### POST `/api/logout`
退出或撤销当前登录状态。
- 通过 `Authorization: Bearer <access_token>` 指定当前访问令牌。
- 撤销当前访问令牌。
- 认证中心读取 `session_id` Cookie。
  - 如果 `session_id` 存在，撤销该 session 关联的所有访问令牌，撤销 session，并通过响应清理 `session_id` Cookie。
  - 如果 `session_id` 不存在，只撤销当前访问令牌。
- 应用退出时，请求不携带 `session_id` Cookie，则只撤销当前应用访问令牌。
- 认证中心退出时，请求携带 `session_id` Cookie，则同时退出认证中心登录态。

### 用户接口
用户接口通过 `Authorization: Bearer <access_token>` 认证当前用户。

#### GET `/api/userinfo`
获取当前用户信息。
- 认证中心校验访问令牌。
- 校验成功后返回当前用户的用户名、邮箱和角色列表。
- 校验失败则返回 `401`。

#### POST `/api/register`
提交注册。
- 请求体包含 `email`、`code`、`username`、`password`。
- 认证中心校验邮箱是否已注册、邮箱验证码是否正确、用户名和密码是否合法。
- 注册成功后创建用户和 session 记录，并通过响应写入 `session_id` Cookie；前端负责跳回 `/api/authorize` 并原样携带授权参数。
- 注册失败则返回错误响应，不写入 `session_id` Cookie。

#### POST `/api/send_email_code`
发送邮箱验证码。
- 请求体包含 `email`、`type`，`type` 支持 `register`、`reset_email`、`reset_password`。
- 认证中心根据验证码类型校验邮箱状态，并生成短有效期验证码发送到用户邮箱。
- 发送成功后返回成功响应。
- 发送失败则返回错误响应。

#### POST `/api/update_username`
修改当前用户用户名。
- 请求体包含 `username`。
- 认证中心校验访问令牌，并校验新用户名是否合法。
- 修改成功后返回成功响应。
- 校验失败则返回错误响应。

#### POST `/api/update_email`
修改当前用户邮箱。
- 请求体包含 `email`、`code`。
- 认证中心校验访问令牌、邮箱验证码和新邮箱状态。
- 修改成功后更新邮箱。
- 校验失败则返回错误响应。

#### POST `/api/update_password`
重置密码。
- 请求体包含 `email`、`code`、`password`，用于忘记密码页或通过邮箱验证码修改密码。
- 认证中心校验邮箱是否存在、验证码是否正确、新密码是否合法。
- 校验成功后更新用户密码，并使该用户已有 session 和访问令牌失效。
- 校验失败则返回错误响应。

### 用户角色权限管理接口
用户角色权限管理接口统一通过 `Authorization: Bearer <access_token>` 认证，且访问令牌必须包含管理员权限 `*`。

#### 用户管理
##### POST `/api/admin/create_user`
创建用户。
- 请求体包含 `email`、`username`、`password`。
- 校验邮箱、用户名和密码是否合法。
- 创建成功后返回用户信息。
- 校验失败则返回错误响应。

##### POST `/api/admin/update_user`
更新用户信息。
- 请求体包含 `user_id`，可选 `email`、`username`、`password`、`yn`。
- 根据传入字段更新用户信息。
- 如果修改密码或禁用用户，应使该用户已有 session 和访问令牌失效。
- 更新成功后返回用户信息。
- 校验失败则返回错误响应。

##### POST `/api/admin/remove_user`
彻底删除用户。
- 请求体包含 `user_id`。
- 删除前撤销该用户已有 session 和访问令牌。
- 删除用户及其所有认证中心关联数据，包括用户角色关系、session、授权码、访问令牌等。
- 操作失败则返回错误响应。

##### GET `/api/admin/list_users`
查询用户列表。
- 查询参数包含 `offset`、`limit`、`keyword`、`all`。
- 返回用户总数和用户列表。

##### GET `/api/admin/user/{user_id}`
查询用户详情。
- 返回用户信息、所属角色和拥有权限。
- 用户不存在则返回错误响应。

#### 角色管理
##### POST `/api/admin/create_role`
创建角色。
- 请求体包含 `name`。
- 创建成功后返回角色信息。
- 校验失败则返回错误响应。

##### POST `/api/admin/update_role`
更新角色信息。
- 请求体包含 `role_id`，可选 `name`、`yn`。
- 更新成功后返回角色信息。
- 如果角色权限状态变化影响用户权限，应同步更新相关用户的访问令牌权限信息。
- 校验失败则返回错误响应。

##### POST `/api/admin/remove_role`
彻底删除角色。
- 请求体包含 `role_id`。
- 删除角色及其所有关联数据，包括用户角色关系、角色权限关系等。
- 删除成功后同步更新受影响用户的访问令牌权限信息。
- 操作失败则返回错误响应。

##### GET `/api/admin/list_roles`
查询角色列表。
- 查询参数包含 `offset`、`limit`、`keyword`、`all`。
- 返回角色总数和角色列表。

##### GET `/api/admin/role/{role_id}`
查询角色详情。
- 返回角色信息、角色内用户和角色权限。
- 角色不存在则返回错误响应。

#### 权限管理
##### POST `/api/admin/create_permission`
创建权限。
- 请求体包含 `name`、可选 `description`。
- 创建成功后返回权限信息。
- 校验失败则返回错误响应。

##### POST `/api/admin/update_permission`
更新权限信息。
- 请求体包含 `permission_id`，可选 `name`、`description`、`yn`。
- 更新成功后返回权限信息。
- 如果权限状态变化影响用户权限，应同步更新相关用户的访问令牌权限信息。
- 校验失败则返回错误响应。

##### POST `/api/admin/remove_permission`
彻底删除权限。
- 请求体包含 `permission_id`。
- 删除权限及其所有关联数据，包括角色权限关系等。
- 删除成功后同步更新受影响用户的访问令牌权限信息。
- 操作失败则返回错误响应。

##### GET `/api/admin/list_permissions`
查询权限列表。
- 查询参数包含 `offset`、`limit`、`keyword`、`all`。
- 返回权限总数和权限列表。

##### GET `/api/admin/permission/{permission_id}`
查询权限详情。
- 返回权限信息、拥有该权限的角色和用户。
- 权限不存在则返回错误响应。

#### 关系管理
##### POST `/api/admin/user-role/add`
批量添加用户与角色关系。
- 请求体包含 `relations`，每项包含 `user_id`、`role_id`。
- 添加成功后同步更新相关用户的访问令牌权限信息。
- 校验失败则返回错误响应。

##### POST `/api/admin/user-role/remove`
批量删除用户与角色关系。
- 请求体包含 `relations`，每项包含 `user_id`、`role_id`。
- 删除成功后同步更新相关用户的访问令牌权限信息。
- 校验失败则返回错误响应。

##### POST `/api/admin/role-permission/add`
批量添加角色与权限关系。
- 请求体包含 `relations`，每项包含 `role_id`、`permission_id`。
- 添加成功后同步更新相关用户的访问令牌权限信息。
- 校验失败则返回错误响应。

##### POST `/api/admin/role-permission/remove`
批量删除角色与权限关系。
- 请求体包含 `relations`，每项包含 `role_id`、`permission_id`。
- 删除成功后同步更新相关用户的访问令牌权限信息。
- 校验失败则返回错误响应。
