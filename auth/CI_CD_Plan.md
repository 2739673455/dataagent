# Auth 项目 CI/CD + Fly.io 部署方案

## 现状

- **auth** 是 FastAPI Python 3.12+ 服务，端口 7100
- 有 2 个前端：`web/`（用户端 React SPA）和 `platform/`（管理端 React SPA）
- 后端通过 `app/presentation/frontend/router.py` 从 `app/static/dist/` 提供 SPA
- 用 `uv` 管理 Python 依赖，`npm` 管理前端依赖
- 已有 `pyrightconfig.json`，无 ruff 配置，无测试
- 无 Dockerfile，无 GitHub Actions

## 需要创建的文件

### 1. `auth/Dockerfile`
多阶段构建：
- **Stage 1**：用 `node:22-alpine` 构建 `web/` 前端 → `npm ci && npm run build`
- **Stage 2**：用 `ghcr.io/astral-sh/uv:python3.12-bookworm` 作为基础镜像，只装生产依赖，复制代码和前端构建产物
- 前端产物放到 `app/static/dist/`
- 入口：`uv run uvicorn app.main:app --host 0.0.0.0 --port 7100`

### 2. `auth/.dockerignore`
排除 `.venv`、`node_modules`、`logs`、`data`、`configs/.env`（本地机密；CI 构建时 .env 由 secrets 注入 ）等

### 3. `auth/fly.toml`
Fly.io 应用配置：
- `app = "insight-auth"`
- `primary_region = "nrt"`（东京，离国内近）
- `internal_port = 7100`
- 默认 1 个 256MB 实例

### 5. `.github/workflows/auth-ci.yml`
触发：push/PR 到任意分支，`auth/**` 路径有变化时
Job：
- Python lint（ruff check）
- Python type check（pyright）
- Frontend lint（biome lint，在 web/ 目录）

### 6. `.github/workflows/auth-cd.yml`
触发：push 到 `main` 分支，`auth/**` 路径有变化时
Job：
- Checkout 代码
- 用 `flyctl` action 执行 `flyctl deploy`（Fly.io 自动 build Docker 并部署）

## 部署前用户需要手动做的一件事

在 repo 根目录运行 `flyctl launch` 创建 Fly.io app，然后设置密钥：
```
fly secrets set \
  --app insight-auth \
  AUTH_SECRET_KEY=<生成一个强密码> \
  ADMIN_EMAIL=<admin email> \
  ADMIN_USERNAME=<admin username> \
  ADMIN_PASSWORD=<admin password> \
  DB_DRIVER=sqlite \
  DB_SQLITE_FILE=data/auth.db \
  SMTP_USER=<smtp email> \
  SMTP_PASSWORD=<smtp password>
```

如果要用 MySQL 则需要自己准备数据库连接信息。

## 注意

- `platform/` 前端**暂不构建**进 Docker，因为 `frontend/router.py` 只提供一个 SPA 挂载点。后续如果 platform 需要独立部署，另开一个 Fly.io app
- 生产环境 cookie 配置需调整：`cookie.secure` 默认 `false`，部署到 Fly.io 后应改为 `true`（Fly.io 提供 HTTPS）
- Fly.io 免费额度：3 个 256MB 实例。auth 用 1 个完全够

## 验证

1. push 到 dev 分支 → CI workflow 自动跑，检查 lint 通过
2. merge 到 main → CD workflow 自动构建 Docker 并部署
3. 访问 `https://insight-auth.fly.dev/` 看到 auth 前端页面
