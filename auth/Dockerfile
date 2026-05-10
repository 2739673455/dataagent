# syntax=docker/dockerfile:1

FROM oven/bun:1-alpine AS web-build

WORKDIR /src/web

COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile

COPY web/ ./
RUN bun run build


FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ ./app/
COPY configs/ ./configs/
COPY sql/ ./sql/
RUN uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

COPY --from=builder /app/.venv/ .venv/
COPY --from=builder /app/app/ app/
COPY --from=builder /app/configs/ configs/
COPY --from=builder /app/sql/ sql/
COPY --from=web-build /src/web/dist/ app/static/dist/

EXPOSE 7100

CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7100"]
