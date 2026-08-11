# 本地 Docker 沙盒

沙盒镜像在构建阶段安装数据分析依赖，运行中的用户容器默认使用
`network_mode: none`，不能临时从 PyPI、npm 或外部网站下载内容。

## 增加依赖

- Python：将包加入 `requirements.txt`。
- Node.js：将包加入 `package.json` 的 `dependencies`。
- 修改依赖后重启服务。`sandbox.rebuild_image: true` 时，管理器会重新执行镜像构建；
  Docker 会复用未变化的构建层。

构建阶段的 Node 下载地址、PyPI 镜像和 npm registry 分别由
`node_download_base`、`pypi_index_url` 和 `npm_registry` 配置。它们只影响镜像构建，
不会为运行中的沙盒开启网络。

## 隔离与限制

- 每个用户对应一个容器和一个持久化 Docker volume。
- 同一用户的每个会话对应独立目录，并使用独立 Linux UID 与 `0700` 权限；隔离由内核权限完成，
  不依赖从 shell 命令中提取路径。
- 容器以非 root 用户运行，根文件系统只读，移除 capabilities，并启用
  `no-new-privileges`、内存、CPU、进程数和执行超时限制。
- 文件、工作区、命令输出和大结果捕获上限在 `conf/app_config.yaml` 的 `sandbox` 节配置。
- 每个会话使用独立的 `HOME`、缓存和 `TMPDIR`，命令默认执行 `umask 077`。
- HTTP 附件上传和下载通过 Docker Archive API 完成，不会启动已停止的用户容器。
- 容器规格发生变化时会重建容器，但保留用户 volume；空闲 10 分钟后停止容器，
  空闲 1 小时后删除容器但保留 volume。
- 同时运行的容器受全局上限控制；容量不足时回收空闲 LRU 容器，所有槽位活跃时新任务排队。

Docker named volume 本身没有跨文件系统通用的硬配额。当前实现会对上传、写入、编辑做写前校验，
对 shell 进程设置单文件上限，并在命令前后检查会话工作区总量。若部署环境要求不可短暂超出的硬磁盘配额，
还需要在 Docker 数据目录所在的宿主文件系统上配置 project quota，或为每个用户挂载带配额的独立卷。
