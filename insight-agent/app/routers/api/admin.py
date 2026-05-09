from fastapi import APIRouter

from app.agent.agent import reset_agent
from app.core.settings import reload_config

router = APIRouter(tags=["admin"])


@router.post("/reload")
async def reload_model_config():
    """热更新模型配置：重新加载 .env 和 config.yml，重建 Agent"""
    reload_config()
    await reset_agent()
    return {"status": "ok", "message": "配置已重新加载，Agent 将在下次请求时重建"}
