# 本地 Docker 沙箱

沙箱镜像在构建阶段安装数据分析依赖，运行中的用户容器默认使用
`network_mode: none`，不能临时从 PyPI、npm 或外部网站下载内容。
镜像预装 `WenQuanYi Zen Hei` 字体，Matplotlib 和 HTML 渲染可以直接使用该字体，
Pillow 使用 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc` 字体文件。

## 增加依赖

- Python：将包加入 `requirements.txt`。
- Node.js：将包加入 `package.json` 的 `dependencies`。
- 修改依赖或 Dockerfile 后执行 `docker compose -f docker/compose.yml build sandbox-image`，再重启 API 和需要访问沙箱的 Worker。
- 日常启动和重启服务直接复用 `dataagent-sandbox:latest`，无需重新构建。

首次执行 `docker compose -f docker/compose.yml up -d` 时，Compose 会在镜像缺失时自动构建沙箱镜像。`sandbox-image` 配置为零副本，只负责声明镜像构建规则，不会创建固定沙箱容器。

镜像名称、构建上下文、构建网络和依赖下载源集中定义在 `docker/compose.yml` 的
`sandbox-image` 服务中。`SANDBOX_APT_MIRROR`、`SANDBOX_APT_SECURITY_MIRROR`、
`SANDBOX_PYPI_INDEX_URL` 和 `SANDBOX_NPM_REGISTRY` 环境变量可以临时覆盖默认下载源。
Node.js 和 npm 随字体一起从 Debian APT 源安装，避免额外下载 Node 镜像或发布包。上述配置只影响镜像构建，
不会为运行中的沙箱开启网络。

```bash
docker compose -f docker/compose.yml build sandbox-image

# 临时覆盖镜像名称或构建源
SANDBOX_IMAGE=dataagent-sandbox:dev \
SANDBOX_NPM_REGISTRY=https://registry.npmjs.org \
docker compose -f docker/compose.yml build sandbox-image
```

应用启动阶段只连接 Docker 并读取 `sandbox.image`。跳过完整 Compose 启动且镜像不存在时，
应用会提示执行 `docker compose -f docker/compose.yml up -d`。

## 隔离与限制

- 每个用户对应一个容器和一个持久化 Docker volume。
- 同一用户的每个会话和 Agent Session 使用独立 Linux UID；会话与 Session 目录使用 `0750`，私有 HOME、缓存和临时目录使用 `0700`；隔离由内核权限完成，
  不依赖从 shell 命令中提取路径。
- 容器以非 root 用户运行，根文件系统只读，移除 capabilities，并启用
  `no-new-privileges`、内存、CPU、进程数和执行超时限制。
- 文件、工作区、命令输出和大结果捕获上限在 `conf/app_config.yaml` 的 `sandbox` 节配置。
- 每个会话使用独立的 `HOME`、缓存和 `TMPDIR`，命令默认执行 `umask 077`。
- HTTP 附件上传和下载通过 Docker Archive API 完成，不会启动已停止的用户容器。
- 容器规格发生变化时会重建容器，但保留用户 volume；空闲 10 分钟后停止容器，
  空闲 1 小时后删除容器但保留 volume。
- 同时运行的容器受全局上限控制；容量不足时回收空闲 LRU 容器，所有槽位活跃时新任务排队。
- 容量等待使用有界 FIFO 队列，支持超时以及任务取消、用户删除和服务关闭取消。
- 容器和 volume 名称包含 `deployment_namespace`，共享 Docker 主机的多个部署实例相互隔离。
- 最近活动时间同时保存在 Redis 和用户 volume 中，服务重启后继续执行原 TTL。
- API 与 Celery Worker 通过 Redis 运行实例租约、操作租约和维护锁共享同一组容器；只有最后退出的运行实例执行容器停止收尾。

Docker named volume 本身没有跨文件系统通用的硬配额。当前实现会对上传、写入、编辑做写前校验，
对 shell 进程设置单文件上限，并在命令前后检查会话工作区总量。若部署环境要求不可短暂超出的硬磁盘配额，
将 `workspace_quota_mode` 设置为 `volume_driver`，并配置支持容量参数的外部 Docker Volume Driver。
管理器会把 `{max_workspace_bytes}` 渲染到驱动参数，并拒绝普通 `local` 驱动、缺少容量参数或存储策略不匹配的已有 volume。
