ARG UV_VERSION=0.12.3

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.14-slim

ARG APT_MIRROR=https://deb.debian.org/debian
ARG APT_SECURITY_MIRROR=https://deb.debian.org/debian-security
ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG NPM_REGISTRY=https://registry.npmjs.org

COPY --from=uv /uv /uvx /usr/local/bin/

RUN sed --in-place \
        --expression="s|http://deb.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
        --expression="s|https://deb.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
        --expression="s|http://deb.debian.org/debian|${APT_MIRROR}|g" \
        --expression="s|https://deb.debian.org/debian|${APT_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get \
        -o Acquire::Retries=3 \
        -o Acquire::http::Timeout=20 \
        -o Acquire::https::Timeout=20 \
        update \
    && apt-get \
        -o Acquire::Retries=3 \
        -o Acquire::http::Timeout=20 \
        -o Acquire::https::Timeout=20 \
        install --yes --no-install-recommends \
        fontconfig \
        fonts-wqy-zenhei \
    && fc-cache --force \
    && test -r /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc \
    && fc-match "WenQuanYi Zen Hei" | grep --quiet "wqy-zenhei" \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get \
        -o Acquire::Retries=3 \
        -o Acquire::http::Timeout=20 \
        -o Acquire::https::Timeout=20 \
        update \
    && apt-get \
        -o Acquire::Retries=3 \
        -o Acquire::http::Timeout=20 \
        -o Acquire::https::Timeout=20 \
        install --yes --no-install-recommends \
        nodejs \
        npm

COPY requirements.txt /tmp/sandbox-requirements.txt

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    UV_HTTP_TIMEOUT=60 uv pip install \
        --system \
        --index-url "${PYPI_INDEX_URL}" \
        --requirements /tmp/sandbox-requirements.txt \
    && rm -f /tmp/sandbox-requirements.txt

COPY package.json package-lock.json /opt/dataagent-node/

RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    cd /opt/dataagent-node \
    && npm ci \
        --omit=dev \
        --ignore-scripts \
        --no-audit \
        --no-fund \
        --fetch-retries=3 \
        --fetch-timeout=60000 \
        --registry="${NPM_REGISTRY}"

RUN useradd --create-home --uid 1000 sandbox \
    && mkdir -p /data \
    && chmod 0711 /data \
    && chown -R sandbox:sandbox /data

ENV NODE_PATH=/opt/dataagent-node/node_modules

USER sandbox
WORKDIR /data

CMD ["sleep", "infinity"]
