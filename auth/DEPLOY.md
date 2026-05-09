# Auth 服务部署文档
镜像仓库：**ghcr.io/\<github-owner\>/\<repo-name\>/auth**

## 镜像发布规则
| 触发               | 推送 tag                  |
| ------------------ | ------------------------- |
| push `main`        | `latest`, `sha-<git-sha>` |
| 创建 `auth-v*` tag | `auth-v0.1.0`             |

## 运行时环境变量
镜像启动时由平台注入：

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

生产 HTTPS：

```bash
COOKIE_SECURE=true
```

## 通用 Docker 运行
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

## 注意事项
- SQLite 必须挂载持久化 volume，不要放在镜像内部
- 镜像不含 `.env`、数据库文件、日志等运行时状态
