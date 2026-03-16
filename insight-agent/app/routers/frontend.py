from pathlib import Path

import httpx
from app.config import CFG
from app.utils.http_client import get_http_client
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# 前端构建产物目录
APP_DIR = Path(__file__).resolve().parent.parent
STATIC_DIST_DIR = APP_DIR / "static" / "dist"
STATIC_ASSETS_DIR = STATIC_DIST_DIR / "assets"
SPA_ENTRY_FILE = STATIC_DIST_DIR / "index.html"

# 这些前缀由后端接口、静态资源或文档页占用，不能回退到 SPA
SPA_EXCLUDED_PREFIXES = (
    "/api",
    "/app-api",
    "/app-ws",
    "/auth-api",
    "/assets",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)

router = APIRouter()


def _matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


@router.api_route(
    "/auth-api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_auth_api(path: str, request: Request) -> Response:
    # 将前端访问的 /auth-api 请求转发到独立认证服务
    upstream_url = f"{CFG.auth_service.base_url.rstrip('/')}/{path.lstrip('/')}"
    client = get_http_client()
    body = await request.body()
    try:
        upstream_response = await client.request(
            request.method,
            upstream_url,
            content=body or None,
            params=request.query_params,
            headers={
                key: value
                for key, value in request.headers.items()
                if key.lower() not in {"host", "content-length"}
            },
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Auth service unavailable: {exc}",
        ) from exc

    response = Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
    )
    for key, value in upstream_response.headers.multi_items():
        if key.lower() in {
            "content-length",
            "content-encoding",
            "transfer-encoding",
            "connection",
        }:
            continue
        response.raw_headers.append((key.encode("latin-1"), value.encode("latin-1")))
    return response


@router.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # SPA 前端路由回退：未命中后端接口时统一返回 index.html
    request_path = f"/{full_path}" if full_path else "/"
    # 后端专用路径不能错误回退到前端首页，命中这些前缀时直接返回 404
    if any(_matches_prefix(request_path, prefix) for prefix in SPA_EXCLUDED_PREFIXES):
        raise HTTPException(status_code=404)
    # 前端尚未构建或产物缺失时，明确返回 404，避免返回无意义的空响应
    if not SPA_ENTRY_FILE.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(SPA_ENTRY_FILE)


def register_frontend(app: FastAPI) -> None:
    # 挂载构建后的静态资源，并注册前端相关路由
    app.mount(
        "/assets",
        StaticFiles(directory=STATIC_ASSETS_DIR, check_dir=False),
        name="assets",
    )
    app.include_router(router)
