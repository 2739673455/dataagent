# syntax=docker/dockerfile:1

FROM oven/bun:1-alpine AS web-build

WORKDIR /src/web

COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile

COPY web/ ./
RUN bun run build


FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ ./app/
COPY configs/ ./configs/
COPY sql/ ./sql/
COPY --from=web-build /src/web/dist/ ./app/static/dist/

EXPOSE 7100

CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7100"]
