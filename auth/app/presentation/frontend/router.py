"""前端 SPA 静态文件服务与路由回退"""

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# 前端构建产物目录
CURRENT_DIR = Path(__file__).resolve().parent
APP_DIR = CURRENT_DIR.parent.parent
STATIC_DIST_DIR = APP_DIR / "static" / "dist"
STATIC_ASSETS_DIR = STATIC_DIST_DIR / "assets"
SPA_ENTRY_FILE = STATIC_DIST_DIR / "index.html"

# 这些前缀由后端接口、静态资源或文档页占用，不能回退到 SPA
SPA_EXCLUDED_PREFIXES = ("/api", "/assets", "/docs", "/openapi.json", "/redoc")

router = APIRouter()


def _matches_prefix(path: str, prefix: str) -> bool:
    """判断请求路径是否命中排除前缀"""
    return path == prefix or path.startswith(f"{prefix}/")


@router.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """SPA 前端路由回退：未命中后端接口时统一返回 index.html"""
    request_path = f"/{full_path}" if full_path else "/"
    # 后端专用路径不能错误回退到前端首页，命中这些前缀时直接返回 404
    if any(_matches_prefix(request_path, prefix) for prefix in SPA_EXCLUDED_PREFIXES):
        raise HTTPException(status_code=404)
    # 前端尚未构建或产物缺失时，明确返回 404，避免返回无意义的空响应
    if not SPA_ENTRY_FILE.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(SPA_ENTRY_FILE)


def register_frontend(app: FastAPI) -> None:
    """挂载前端构建产物静态资源并注册 SPA 回退路由"""
    app.mount(
        "/assets",
        StaticFiles(directory=STATIC_ASSETS_DIR, check_dir=False),
        name="assets",
    )
    app.include_router(router)
