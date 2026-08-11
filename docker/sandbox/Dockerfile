ARG UV_VERSION=0.12.3

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.14-slim

ARG NODE_VERSION=20.19.2
ARG NODE_DOWNLOAD_BASE=https://nodejs.org/dist
ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG NPM_REGISTRY=https://registry.npmjs.org
ARG TARGETARCH=amd64

COPY --from=uv /uv /uvx /usr/local/bin/

RUN export NODE_ARCH="$(case "${TARGETARCH}" in amd64) echo x64 ;; arm64) echo arm64 ;; *) exit 1 ;; esac)" \
    && export NODE_FILENAME="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" \
    && export NODE_VERSION NODE_DOWNLOAD_BASE \
    && python - <<'PY'
import hashlib
import os
import pathlib
import shutil
import tarfile
import urllib.request

version = os.environ["NODE_VERSION"]
base_url = os.environ["NODE_DOWNLOAD_BASE"].rstrip("/")
filename = os.environ["NODE_FILENAME"]
release_url = f"{base_url}/v{version}"
archive_path = pathlib.Path("/tmp") / filename
with urllib.request.urlopen(f"{release_url}/{filename}", timeout=60) as response:
    with archive_path.open("wb") as archive_file:
        shutil.copyfileobj(response, archive_file)
checksums = urllib.request.urlopen(f"{release_url}/SHASUMS256.txt", timeout=60).read().decode()
expected = next(line.split()[0] for line in checksums.splitlines() if line.endswith(f"  {filename}"))
actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
if actual != expected:
    raise RuntimeError(f"Node.js checksum mismatch: {actual} != {expected}")
with tarfile.open(archive_path, "r:xz") as archive:
    archive.extractall("/tmp/node", filter="data")
source_dir = pathlib.Path("/tmp/node") / filename.removesuffix(".tar.xz")
shutil.copytree(source_dir, "/usr/local", dirs_exist_ok=True, symlinks=True)
archive_path.unlink()
shutil.rmtree("/tmp/node")
PY

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
    && mkdir -p /workspace/conversations \
    && chmod 0711 /workspace/conversations \
    && chown -R sandbox:sandbox /workspace

ENV NODE_PATH=/opt/dataagent-node/node_modules

USER sandbox
WORKDIR /workspace

CMD ["sleep", "infinity"]
