"""提供预定义用户列表。"""

from fastapi import APIRouter

from app.dependencies import WebResourcesDep
from app.identity.api.schemas import UserResponse
from app.identity.repositories.identity import IdentityPGRepo

router = APIRouter(tags=["用户"])


@router.get("", response_model=list[UserResponse])
async def list_users(resources: WebResourcesDep) -> list[UserResponse]:
    """返回已初始化用户的 ID、用户名和角色名。"""
    async with resources.auth.session() as session:
        users = await IdentityPGRepo(session).list_users()
        return [UserResponse.model_validate(user) for user in users]
