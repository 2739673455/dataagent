# Auth 服务部署文档
镜像仓库：**ghcr.io/\<github-owner\>/\<repo-name\>/auth**

## 镜像发布规则
| 触发               | 推送 tag                  |
| ------------------ | ------------------------- |
| push `main`        | `latest`, `sha-<git-sha>` |
| 创建 `auth-v*` tag | `auth-v0.1.0`             |

## Docker 运行
```bash
docker run -d --name auth-server \
  -p 7100:7100 \
  -e AUTH_SECRET_KEY=[ ] \
  -e ADMIN_EMAIL=admin@123.com \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD='123123' \
  -e DB_DRIVER=sqlite \
  -e DB_SQLITE_FILE=/data/auth.db \
  -e SMTP_USER=[ ] \
  -e SMTP_PASSWORD=[ ] \
  -e COOKIE_SECURE=false \
  -v auth-data:/data \
  ghcr.io/<github-owner>/<repo-name>/auth:latest
```
