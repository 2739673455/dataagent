from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.model_config import (
    CanCreateModelConfigRequest,
    CanCreateModelConfigResponse,
    CreateModelConfigRequest,
    DeleteModelConfigRequest,
    ModelConfigListResponse,
    ModelConfigResponse,
    UpdateModelConfigRequest,
)
from app.schemas.user import AccessTokenPayload
from app.services.auth import authenticate_access_token
from app.services.database import get_app_db
from app.services.model_config import (
    create_model_config,
    delete_model_configs,
    get_model_configs,
    update_model_config,
)
from app.utils.crypto import decrypt, encrypt
from app.utils.log import app_logger

router = APIRouter(prefix="/model_config", tags=["模型配置管理"])


@router.get("", response_model=ModelConfigListResponse)
async def api_get_model_configs(
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> ModelConfigListResponse:
    """获取模型配置列表"""
    model_configs = await get_model_configs(db_session, payload.sub)
    app_logger.info(f"User get model configs: {[i.id for i in model_configs]}")
    return ModelConfigListResponse(
        configs=[
            ModelConfigResponse(
                config_id=i.id,
                name=i.name,
                base_url=i.base_url,
                model_name=i.model_name,
                api_key=decrypt(i.encrypted_api_key),
                params=i.params,
            )
            for i in model_configs
        ]
    )


@router.post("/can_create", response_model=CanCreateModelConfigResponse)
async def api_can_create_model_config(
    request: CanCreateModelConfigRequest,
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> CanCreateModelConfigResponse:
    """检查是否能创建新的模型配置"""
    limit = 3  # 普通用户最多创建3个模型配置
    if ("add_more_model_config" not in payload.scope) and (
        request.config_count >= limit
    ):
        return CanCreateModelConfigResponse(can_create=False, limit=limit)
    return CanCreateModelConfigResponse(can_create=True, limit=limit)


@router.post(
    "/create", status_code=status.HTTP_201_CREATED, response_model=ModelConfigResponse
)
async def api_create_model_config(
    request: CreateModelConfigRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> ModelConfigResponse:
    """创建模型配置"""
    model_config = await create_model_config(
        db_session,
        payload.sub,
        request.name,
        request.base_url,
        request.model_name,
        encrypt(request.api_key),
        request.params,
    )
    app_logger.info(f"User create model config: {model_config.id}")
    return ModelConfigResponse(
        config_id=model_config.id,
        name=model_config.name,
        base_url=model_config.base_url,
        model_name=None,
        api_key=None,
        params=None,
    )


@router.post("/update", status_code=status.HTTP_202_ACCEPTED)
async def api_update_model_config(
    request: UpdateModelConfigRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
):
    """修改模型配置"""
    app_logger.info(f"User update model config: {request.config_id}")
    await update_model_config(
        db_session,
        request.config_id,
        request.name,
        request.base_url,
        request.model_name,
        encrypt(request.api_key),
        request.params,
    )


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_model_configs(
    request: DeleteModelConfigRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
):
    """批量删除模型配置"""
    app_logger.info(f"User delete model configs: {request.ids}")
    await delete_model_configs(db_session, request.ids)
