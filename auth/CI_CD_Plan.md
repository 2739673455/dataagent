# Auth 项目 CI/CD + 镜像发布方案
## 目标
把 **auth** 服务做成可复用的 Docker 镜像，并通过 GitHub Actions 自动检查、构建、发布到镜像仓库。

镜像仓库使用 **GitHub Container Registry (GHCR)**：

```text
ghcr.io/<github-owner>/<repo-name>/auth
```

## 现状
- **auth** 是 FastAPI Python 3.12+ 服务，默认端口 `7100`
- 有 2 个前端：`web/`（用户端 React SPA）和 `platform/`（管理端 React SPA）
- 后端通过 `app/presentation/frontend/router.py` 从 `app/static/dist/` 提供 SPA
- 用 `uv` 管理 Python 依赖，`bun` 管理前端依赖
- 已有 `pyrightconfig.json` 和 `pyproject.toml` 中的 ruff 配置
- 已有 `Dockerfile` 和 `.dockerignore`
- 已有 `.github/workflows/auth-ci.yml`
- 已有 `.github/workflows/auth-image.yml`
- 当前没有测试

## 总体流程
1. push 到 `main`：构建并推送生产镜像
   - 推送 `ghcr.io/<owner>/<repo>/auth:latest`
   - 推送 `ghcr.io/<owner>/<repo>/auth:<git-sha>`

2. 创建 Git tag，例如 `auth-v0.1.0`：构建并推送版本镜像
   - 推送 `ghcr.io/<owner>/<repo>/auth:auth-v0.1.0`

3. 任意部署平台只拉取镜像运行
   - 平台负责 HTTPS、域名、运行时环境变量、持久化存储
   - 镜像本身不包含 `.env`、数据库文件、日志文件等运行时状态

## 已创建的文件
### `.github/workflows/auth-image.yml`
触发条件：

- `push` 到 `main`，且 `auth/**` 有变化
- `push` tag：`auth-v*`
- 手动触发：`workflow_dispatch`

核心步骤：

- `actions/checkout@v4`
- `docker/setup-buildx-action@v3` 启用 Docker Buildx
- `docker/login-action@v3` 登录 GHCR
- `docker/metadata-action@v5` 生成镜像标签
- `docker/build-push-action@v6` 构建并推送 `./auth`，并使用 GitHub Actions cache 加速后续构建

权限：

```yaml
permissions:
  contents: read
  packages: write
```

推荐标签规则：

- `main` 分支：`latest`
- 每次提交：`sha-<short-sha>`
- Git tag：`auth-v0.1.0`

镜像示例：

```text
ghcr.io/<github-owner>/<repo-name>/auth:latest
ghcr.io/<github-owner>/<repo-name>/auth:sha-a1b2c3d
ghcr.io/<github-owner>/<repo-name>/auth:auth-v0.1.0
```

## 运行时配置
镜像启动时需要由平台注入以下环境变量：

```bash
AUTH_SECRET_KEY=<生成一个强密码>
ADMIN_EMAIL=<admin email>
ADMIN_USERNAME=<admin username>
ADMIN_PASSWORD=<admin password>
DB_DRIVER=sqlite
DB_SQLITE_FILE=<持久化路径，例如 /data/auth.db>
SMTP_USER=<smtp email>
SMTP_PASSWORD=<smtp password>
```

正式上 HTTPS 时，生产环境设置：

```bash
COOKIE_SECURE=true
```

## 部署平台接入方式
### 通用 Docker 运行
```bash
docker pull ghcr.io/<github-owner>/<repo-name>/auth:latest
docker run --rm -p 7100:7100 \
  -e AUTH_SECRET_KEY=... \
  -e ADMIN_EMAIL=... \
  -e ADMIN_USERNAME=... \
  -e ADMIN_PASSWORD=... \
  -e DB_DRIVER=sqlite \
  -e DB_SQLITE_FILE=/data/auth.db \
  -e SMTP_USER=... \
  -e SMTP_PASSWORD=... \
  -v auth-data:/data \
  ghcr.io/<github-owner>/<repo-name>/auth:latest
```

## 数据持久化注意事项
- 镜像文件系统应视为不可变，不要把 SQLite 放在镜像内部路径
- 使用 SQLite 时，必须挂载平台提供的持久化 volume
- 当前代码只真正支持 SQLite 初始化；MySQL 需要补充依赖和初始化逻辑后再列为生产选项
- 多实例部署时不要直接共享单个 SQLite 文件；需要改用外部数据库，或引入明确的 SQLite 复制方案

## 验证
1. 提交 PR：确认 `auth-ci.yml` 全部通过
2. 合并到 `main`：确认 `auth-image.yml` 推送镜像到 GHCR
3. 本地拉取镜像：

```bash
docker pull ghcr.io/<github-owner>/<repo-name>/auth:latest
```

4. 在目标平台配置环境变量和持久化存储
5. 访问服务首页，确认前端页面和 `/api/*` 接口都正常
