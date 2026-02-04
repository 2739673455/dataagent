from contextlib import asynccontextmanager

from app.config import CFG
from app.handlers import register_exception_handlers
from app.middlewares import trace
from app.routers import api
from app.services import database
from app.utils.log import (
    setup_logger,
    # start_background_logging,
    # stop_background_logging,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    # start_background_logging()
    yield
    # stop_background_logging()
    await database.close_all()


app = FastAPI(lifespan=lifespan)

# 添加日志中间件
app.middleware("http")(trace.middleware)
# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=CFG.cors_origins,  # 允许的源列表
    allow_credentials=True,  # 允许 Authorization headers, Cookies
    allow_methods=["*"],  # 允许的 HTTP 方法列表
    allow_headers=["*"],  # 允许的请求头列表
)

# 注册异常处理
register_exception_handlers(app)


@app.get("/health")
async def health():
    return {"status": "healthy"}


app.include_router(api.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=7777, reload=True)
