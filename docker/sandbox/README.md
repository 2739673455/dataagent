# 本地 Docker 沙箱

沙箱镜像在构建阶段安装数据分析依赖，运行中的用户容器默认使用 `network_mode: none`，不能临时从 PyPI、npm 或外部网站下载内容。

镜像预装 `WenQuanYi Zen Hei` 字体，Matplotlib 和 HTML 渲染可以直接使用该字体，Pillow 使用 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc` 字体文件。

## 增加依赖

- Python：将包加入 `requirements.txt`。
- Node.js：将包加入 `package.json` 的 `dependencies`。
- 修改依赖或 Dockerfile 后执行 `docker compose -f docker/compose.yml build sandbox-image`，再重启 API 和需要访问沙箱的 Worker。
- 日常启动和重启服务直接复用 `dataagent-sandbox:latest`，无需重新构建。

首次执行 `docker compose -f docker/compose.yml up -d` 时，Compose 会在镜像缺失时自动构建沙箱镜像。`sandbox-image` 配置为零副本，只负责声明镜像构建规则，不会创建固定沙箱容器。

镜像名称、构建上下文、构建网络和依赖下载源集中定义在 `docker/compose.yml` 的 `sandbox-image` 服务中。`SANDBOX_APT_MIRROR`、`SANDBOX_APT_SECURITY_MIRROR`、`SANDBOX_PYPI_INDEX_URL` 和 `SANDBOX_NPM_REGISTRY` 环境变量可以临时覆盖默认下载源。

Node.js 和 npm 随字体一起从 Debian APT 源安装，避免额外下载 Node 镜像或发布包。上述配置只影响镜像构建，不会为运行中的沙箱开启网络。

```bash
docker compose -f docker/compose.yml build sandbox-image

# 临时覆盖镜像名称或构建源
SANDBOX_IMAGE=dataagent-sandbox:dev \
SANDBOX_NPM_REGISTRY=https://registry.npmjs.org \
docker compose -f docker/compose.yml build sandbox-image
```

应用启动阶段只连接 Docker 并读取 `sandbox.image`。跳过完整 Compose 启动且镜像不存在时，应用会提示执行 `docker compose -f docker/compose.yml up -d`。
