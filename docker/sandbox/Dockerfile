FROM python:3.14-slim

ARG UV_VERSION=0.12.3
ARG TARGETARCH=amd64

RUN UV_ARCH="$(case "${TARGETARCH}" in amd64) echo x86_64 ;; arm64) echo aarch64 ;; *) exit 1 ;; esac)" \
    && export UV_ARCH \
    && python -c "import os, urllib.request; arch = os.environ['UV_ARCH']; urllib.request.urlretrieve(f'https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-{arch}-unknown-linux-gnu.tar.gz', '/tmp/uv.tar.gz')" \
    && tar -xzf /tmp/uv.tar.gz -C /tmp \
    && mv "/tmp/uv-${UV_ARCH}-unknown-linux-gnu/uv" "/tmp/uv-${UV_ARCH}-unknown-linux-gnu/uvx" /usr/local/bin/ \
    && rm -rf /tmp/uv.tar.gz "/tmp/uv-${UV_ARCH}-unknown-linux-gnu" \
    && useradd --create-home --uid 1000 sandbox \
    && mkdir -p /workspace/conversations \
    && chown -R sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace

CMD ["sleep", "infinity"]
