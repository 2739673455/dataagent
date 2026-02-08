from contextlib import asynccontextmanager

from app.config import CFG
from app.handlers import register_exception_handlers
from app.middlewares import trace
from app.routers import api
from app.utils import database
from app.utils.log import setup_logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


# 生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()  # 初始化日志
    yield
    await database.close_all()  # 关闭数据库引擎


app = FastAPI(lifespan=lifespan)

# 日志中间件
app.middleware("http")(trace.middleware)
# CORS 中间件
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
