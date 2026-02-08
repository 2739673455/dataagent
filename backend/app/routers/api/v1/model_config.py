from typing import Annotated

from app.schemas.model_config import (
    CreateModelConfigRequest,
    DeleteModelConfigRequest,
    ModelConfigListResponse,
    ModelConfigResponse,
    UpdateModelConfigRequest,
)
from app.services.model_config import (
    create_model_config,
    delete_model_configs,
    get_model_configs,
    update_model_config,
)
from app.utils.crypto import decrypt, encrypt
from app.utils.database import get_app_db
from app.utils.log import logger
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/model_config", tags=["模型配置管理"])


@router.get("", response_model=ModelConfigListResponse)
async def api_get_model_configs(
    request: Request, db_session: Annotated[AsyncSession, Depends(get_app_db)]
) -> ModelConfigListResponse:
    """获取模型配置列表"""
    model_configs = await get_model_configs(db_session, request.state.payload.sub)
    logger.info(f"User get model configs: {[i.id for i in model_configs]}")
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
    logger.info(f"User create model config: {model_config.id}")
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
):
    """修改模型配置"""
    logger.info(f"User update model config: {request.config_id}")
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
):
    """批量删除模型配置"""
    logger.info(f"User delete model configs: {request.ids}")
    await delete_model_configs(db_session, request.ids)
