ARG UV_VERSION=0.12.3

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.14-slim

ARG NODE_VERSION=20.19.2
ARG NODE_DOWNLOAD_BASE=https://nodejs.org/dist
ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG NPM_REGISTRY=https://registry.npmjs.org
ARG TARGETARCH=amd64

COPY --from=uv /uv /uvx /usr/local/bin/

COPY install_node.py /tmp/install-node.py

RUN export NODE_ARCH="$(case "${TARGETARCH}" in amd64) echo x64 ;; arm64) echo arm64 ;; *) exit 1 ;; esac)" \
    && export NODE_FILENAME="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" \
    && export NODE_VERSION NODE_DOWNLOAD_BASE \
    && python /tmp/install-node.py \
    && rm -f /tmp/install-node.py

COPY requirements.txt /tmp/sandbox-requirements.txt

RUN UV_HTTP_TIMEOUT=60 uv pip install \
        --system \
        --no-cache \
        --index-url "${PYPI_INDEX_URL}" \
        --requirements /tmp/sandbox-requirements.txt \
    && rm -f /tmp/sandbox-requirements.txt

COPY package.json package-lock.json /opt/dataagent-node/

RUN cd /opt/dataagent-node \
    && npm ci \
        --omit=dev \
        --ignore-scripts \
        --fetch-retries=3 \
        --fetch-timeout=60000 \
        --registry="${NPM_REGISTRY}" \
    && npm cache clean --force

RUN useradd --create-home --uid 1000 sandbox \
    && mkdir -p /data \
    && chmod 0711 /data \
    && chown -R sandbox:sandbox /data

ENV NODE_PATH=/opt/dataagent-node/node_modules

USER sandbox
WORKDIR /data

CMD ["sleep", "infinity"]
