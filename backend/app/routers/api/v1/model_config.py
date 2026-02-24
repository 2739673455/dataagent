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
from app.utils.log import logger
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.utils.db import get_app_db

router = APIRouter(prefix="/model_config", tags=["模型配置管理"])


@router.get("")
async def api_get_model_configs(
    request: Request, db_session: Annotated[AsyncSession, Depends(get_app_db)]
) -> ModelConfigListResponse:
    """获取模型配置列表"""
    model_configs = await get_model_configs(db_session, request.state.payload.sub)
    logger.info(
        f"User get model configs: model_config_ids={[i.id for i in model_configs]}"
    )
    return ModelConfigListResponse(
        configs=[
            ModelConfigResponse(
                config_id=i.id,
                name=i.name,
                base_url=i.base_url,
                model_name=i.model_name,
                api_key=i.api_key,
                params=i.params,
            )
            for i in model_configs
        ]
    )


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def api_create_model_config(
    request: Request,
    body: CreateModelConfigRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> ModelConfigResponse:
    """创建模型配置"""
    model_config = await create_model_config(
        db_session=db_session,
        user_id=request.state.payload.sub,
        name=body.name,
        base_url=body.base_url,
        model_name=body.model_name,
        api_key=body.api_key,
        params=body.params,
    )
    logger.info(f"User create model config: model_config_id={model_config.id}")
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
    body: UpdateModelConfigRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> ModelConfigResponse:
    """修改模型配置"""
    logger.info(f"User update model config: model_config_id={body.config_id}")
    model_config = await update_model_config(
        db_session=db_session,
        id=body.config_id,
        name=body.name,
        base_url=body.base_url,
        model_name=body.model_name,
        api_key=body.api_key,
        params=body.params,
    )
    return ModelConfigResponse(
        config_id=model_config.id,
        name=model_config.name,
        base_url=model_config.base_url,
        model_name=None,
        api_key=None,
        params=None,
    )


@router.post("/delete")
async def api_delete_model_configs(
    body: DeleteModelConfigRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> None:
    """批量删除模型配置"""
    logger.info(f"User delete model configs: model_config_ids={body.ids}")
    await delete_model_configs(db_session, body.ids)
