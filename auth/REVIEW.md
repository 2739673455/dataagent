# 认证流程 Review

当前 `auth/README.md` 中的流程整体更接近 OAuth2 授权码流程的简化版，主线方向是对的：

```text
app -> auth/authorize -> auth/login -> auth/authorize -> app/auth/callback -> auth/api/token -> app
```

但如果目标是对齐标准 OIDC Authorization Code Flow，还需要补齐一些关键参数、校验点和安全约束。

## 主要问题

### 1. 授权请求缺少标准参数

当前授权请求中主要包含：

```text
client_id=app
redirect_uri=app/auth/callback?redirect_uri=app/
```

标准 OIDC 授权请求通常至少需要：

```text
response_type=code
client_id=app
redirect_uri=http://app.com/auth/callback
scope=openid profile email
state=<random-state>
nonce=<random-nonce>
code_challenge=<pkce-code-challenge>
code_challenge_method=S256
```

其中：

- `response_type=code` 表示使用授权码模式。
- `scope=openid` 表示这是 OIDC 请求，而不仅仅是 OAuth2 请求。
- `state` 用于防止 CSRF，并关联登录前的应用状态。
- `nonce` 用于绑定 OIDC 登录请求和 `id_token`，防止重放。
- `code_challenge` 和 `code_challenge_method` 用于 PKCE。

### 2. `redirect_uri` 的语义混用了

OIDC 中的 `redirect_uri` 表示认证中心完成授权后回跳到客户端的回调地址，它应该是一个提前注册过的、精确匹配的地址，例如：

```text
http://app.com/auth/callback
```

当前流程里使用了：

```text
app/auth/callback?redirect_uri=app/
```

这里把两个概念混在了一起：

- OIDC 回调地址：认证中心回跳给客户端的位置。
- 登录完成后的最终返回地址：例如 `app/`。

更推荐的方式是：

```text
redirect_uri=http://app.com/auth/callback
state=<random-state>
```

最终要回到 `app/` 的信息可以通过 `state` 关联到服务端或前端临时保存的状态，而不是嵌套在 OIDC 的 `redirect_uri` 参数中。

### 3. 前端直接调用 `introspection` 不太标准

当前流程中，前端认证守卫会调用：

```text
auth/api/introspection
```

来检查访问令牌是否有效。

在 OAuth2/OIDC 中，`introspection` 通常是资源服务器或后端服务用来校验 access token 的接口。它往往需要可信客户端身份认证，不太适合由浏览器前端直接调用。

更常见的做法：

- SPA：使用 Authorization Code + PKCE，前端根据 token 过期时间判断是否需要重新登录或刷新。
- BFF：token 存在后端，浏览器只持有 HttpOnly、Secure、SameSite Cookie。
- 后端接口：资源服务器或应用后端校验 access token，而不是前端负责校验。

### 4. token 请求缺少必要参数

当前 token 请求中主要包含：

```text
code=666
client_id=app
```

标准授权码换 token 请求通常需要：

```text
grant_type=authorization_code
code=<authorization-code>
redirect_uri=http://app.com/auth/callback
client_id=app
code_verifier=<pkce-code-verifier>
```

如果客户端是 confidential client，还需要客户端认证，例如 `client_secret`。如果 `app` 是纯前端 SPA，则不应该依赖 `client_secret`，而应该使用 PKCE。

### 5. OIDC 应返回并校验 `id_token`

OIDC 是 OAuth2 之上的身份层。标准 OIDC token endpoint 通常不仅返回 `access_token`，还会返回 `id_token`：

```json
{
  "access_token": "...",
  "id_token": "...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

客户端拿到 `id_token` 后应校验：

- 签名。
- `iss`。
- `aud`。
- `exp`。
- `iat`。
- `nonce`。

当前流程中通过 `auth/api/me` 获取用户信息，这更像 OAuth2 + UserInfo 的方式。它可以存在，但如果要体现标准 OIDC，仍应补充 `id_token` 的返回和校验。

### 6. 缺少 `state` 和 `nonce`

当前流程没有体现 `state` 和 `nonce`。

建议补充：

```text
state=<random-state>
nonce=<random-nonce>
```

其中：

- `state`：用于 CSRF 防护，并关联登录前的目标页面。
- `nonce`：用于绑定授权请求和 `id_token`，避免重放攻击。

### 7. 授权码应为高熵随机值

文档里使用 `code=666` 作为示例可以理解，但建议明确真实实现中授权码应该是：

- 一次性。
- 短有效期。
- 高熵随机值。
- 使用后立即作废。

授权码还应该绑定以下信息：

- `client_id`。
- `redirect_uri`。
- `code_challenge`。
- `user_id`。
- 过期时间。
- 是否已使用。

### 8. token 存储在 `localStorage` 有安全风险

当前流程把令牌写入前端 `localStorage`。

这种方式实现简单，但存在 XSS 风险：一旦页面存在 XSS 漏洞，攻击者可以读取 `localStorage` 中的 token。

更推荐的方式：

- SPA：access token 尽量放在内存中，减少长期暴露。
- BFF：token 存在服务端，浏览器只持有 HttpOnly、Secure、SameSite Cookie。
- 如果仍使用 `localStorage`，文档中应明确这是一个安全取舍，并强化 XSS 防护。

## 建议调整后的流程

### 1. 访问应用

用户访问：

```text
http://app.com/
```

应用发现当前没有有效登录状态，准备发起 OIDC 授权请求。

### 2. 应用生成授权请求

应用生成下列参数：

```text
response_type=code
client_id=app
redirect_uri=http://app.com/auth/callback
scope=openid profile email
state=<random-state>
nonce=<random-nonce>
code_challenge=<pkce-code-challenge>
code_challenge_method=S256
```

同时应用需要保存或关联：

- `state`。
- `nonce`。
- `code_verifier`。
- 登录完成后的最终返回地址，例如 `http://app.com/`。

### 3. 跳转认证中心授权入口

应用跳转到：

```text
http://auth.com/authorize?response_type=code&client_id=app&redirect_uri=http://app.com/auth/callback&scope=openid profile email&state=...&nonce=...&code_challenge=...&code_challenge_method=S256
```

### 4. 认证中心检查登录态

认证中心检查 `session_id` Cookie：

- 如果已有登录态，继续授权流程。
- 如果没有登录态，跳转到认证中心登录页。

登录页完成登录后，应回到原始授权请求，而不是重新手写一份不完整的授权参数。

### 5. 认证中心生成授权码

认证中心生成一次性、短有效期、高熵随机授权码 `code`，并绑定：

- `client_id`。
- `redirect_uri`。
- `code_challenge`。
- `user_id`。
- `scope`。
- 过期时间。
- 是否已使用。

### 6. 认证中心回跳应用回调页

认证中心跳转到：

```text
http://app.com/auth/callback?code=<authorization-code>&state=<random-state>
```

### 7. 应用校验 `state` 并换取 token

应用回调页先校验 `state` 是否匹配。

校验通过后，请求 token endpoint：

```text
POST http://auth.com/api/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
code=<authorization-code>
redirect_uri=http://app.com/auth/callback
client_id=app
code_verifier=<pkce-code-verifier>
```

认证中心校验：

- 授权码是否存在。
- 授权码是否过期。
- 授权码是否已使用。
- `client_id` 是否匹配。
- `redirect_uri` 是否匹配。
- `code_verifier` 是否能匹配 `code_challenge`。

校验通过后作废授权码，并返回 token。

### 8. 应用校验 `id_token` 并建立前端登录状态

token endpoint 返回：

```json
{
  "access_token": "...",
  "id_token": "...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

应用校验 `id_token` 后建立登录状态，并根据之前通过 `state` 关联的最终返回地址跳转回应用页面。

### 9. 调用业务后端接口

前端请求后端接口时携带 access token：

```text
Authorization: Bearer <access-token>
```

业务后端通过中间件校验 token，获取权限信息并执行鉴权。
