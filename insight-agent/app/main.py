from contextlib import asynccontextmanager

import uvicorn
from app import middlewares, routers
from app.config import CFG
from app.exceptions.handlers import register_exception_handlers
from app.utils import db, redis as redis_util
from app.utils.log import setup_logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    yield
    await redis_util.close()
    await db.close_all()


app = FastAPI(lifespan=lifespan)

# 认证中间件
app.middleware("http")(middlewares.auth.middleware)
# 日志中间件
app.middleware("http")(middlewares.trace.middleware)
# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=CFG.cors_origins,  # 允许的源列表
    allow_credentials=True,  # 允许 Authorization headers, Cookies
    allow_methods=["*"],  # 允许的HTTP方法列表
    allow_headers=["*"],  # 允许的请求头列表
)

# 注册异常处理
register_exception_handlers(app)


@app.get("/health")
async def health():
    return {"status": "healthy"}


app.include_router(routers.api.router)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=CFG.port)
