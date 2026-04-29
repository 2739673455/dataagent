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
- `app` = `http://app.com`
- `auth` = `http://auth.com`

1. 访问应用，跳转到 `app/`
2. 应用首页 `app/`
   - 认证守卫检查前端 localStorage 中是否有访问令牌
   - 有访问令牌，请求 `auth/api/introspection` 检查是否有效，令牌有效则结束，正常访问 `app/`
   - 本地无访问令牌或令牌无效，拼接查询参数
     - `client_id=app`
     - *`redirect_uri=app/auth/callback?redirect_uri=app/`
  -  跳转到认证中心授权入口 `auth/api/authorize`
3. 认证中心授权入口 `auth/api/authorize ? client_id=app & redirect_uri=app/auth/callback?redirect_uri=app/`
   - 认证中心检查登录态 (`session_id` Cookie)
   - 有登录态，继续步骤5
   - 无登录态，透传 `redirect_uri=app/auth/callback?redirect_uri=app/`
   - 跳转到认证中心登录页 `auth/login`
4. 认证中心登录页 `auth/login ? redirect_uri=app/auth/callback?redirect_uri=app/`
   - 登录成功后写入认证中心会话 Cookie
   - 拼接查询参数 `client_id=app`
   - 透传 `redirect_uri=app/auth/callback?redirect_uri=app/`
   - 跳转到认证中心授权入口 `auth/api/authorize`
5. 认证中心授权入口 `auth/api/authorize ? client_id=app & redirect_uri=app/auth/callback?redirect_uri=app/`
   - 认证中心生成一次性授权码 `code=666`，并记录 `user_id + session_id + client_id + redirect_uri`
   - 拼接查询参数
     - `code=666`
   - 跳转到 `redirect_uri` (应用认证回调页)
6. 应用认证回调页 `app/auth/callback ? redirect_uri=app/ & code=666`
   - 回调页拼接请求参数
     - `code=666`
     - `client_id=app`
   - 以表单方式请求访问令牌 `POST auth/api/token`
   - 认证中心校验授权码与 `client_id`，校验通过后作废授权码，返回回调页令牌
   - 回调页调用 `auth/api/introspection` 与 `auth/api/me` 获取权限信息与用户信息，并将令牌写入前端 localStorage
   - 跳转到 `redirect_uri` (应用首页)
7. 应用首页 `app/`
    - 前端请求后端接口时携带 Bearer Token ，后端通过中间件请求认证中心验证令牌，获取权限信息并鉴权

登录页、注册页如果没有 `redirect_uri` 参数，跳转到认证中心授权入口前生成 `redirect_uri={window.location.origin}/auth/callback?redirect_uri=/` 参数  
忘记密码页在完成后跳转到登录页

## RBAC 权限控制

RBAC（Role-Based Access Control）是基于角色的访问控制模型。它的核心思想是：

先把权限分配给角色，再把角色分配给用户。

### 常见结构

用户 User -> 角色 Role -> 权限 Permission

### 数据库表

#### user

用户表，保存系统账号信息。

| 字段            | 说明                                 |
| --------------- | ------------------------------------ |
| `id`            | 用户 ID                              |
| `email`         | 邮箱，唯一                           |
| `name`          | 用户名                               |
| `password_hash` | 密码哈希                             |
| `yn`            | 是否启用，`1` 表示启用，`0` 表示禁用 |
| `created_at`    | 创建时间                             |
| `updated_at`    | 更新时间                             |

#### role

角色表，用来承载一组权限。

| 字段         | 说明                                 |
| ------------ | ------------------------------------ |
| `id`         | 角色 ID                              |
| `name`       | 角色名称，唯一                       |
| `yn`         | 是否启用，`1` 表示启用，`0` 表示禁用 |
| `created_at` | 创建时间                             |
| `updated_at` | 更新时间                             |

#### permission

权限表，表示一个具体的操作权限或访问范围。

| 字段          | 说明                                 |
| ------------- | ------------------------------------ |
| `id`          | 权限 ID                              |
| `name`        | 权限名称，唯一                       |
| `description` | 权限说明                             |
| `yn`          | 是否启用，`1` 表示启用，`0` 表示禁用 |
| `created_at`  | 创建时间                             |
| `updated_at`  | 更新时间                             |

#### user_role_rel

用户与角色关系表。

| 字段      | 说明    |
| --------- | ------- |
| `user_id` | 用户 ID |
| `role_id` | 角色 ID |

#### role_permission_rel

角色与权限关系表。

| 字段            | 说明    |
| --------------- | ------- |
| `role_id`       | 角色 ID |
| `permission_id` | 权限 ID |
