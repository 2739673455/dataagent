import random
from collections.abc import Sequence

from app.entities.chat import ModelConfig
from app.utils.crypto import decrypt, encrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_model_configs(
    db_session: AsyncSession, user_id: int
) -> Sequence[ModelConfig]:
    """获取模型配置列表"""
    stmt = select(ModelConfig).where(ModelConfig.user_id == user_id)
    result = await db_session.execute(stmt)
    model_configs = result.scalars().all()
    for i in model_configs:
        i.api_key = decrypt(i.api_key)
    return model_configs


async def create_model_config(
    db_session: AsyncSession,
    user_id: int,
    name: str | None,
    base_url: str,
    model_name: str | None,
    api_key: str | None,
    params: dict | None,
) -> ModelConfig:
    """创建模型配置"""
    model_config = ModelConfig(
        name=name or model_name or f"Model Config {random.randint(0, 1000)}",
        base_url=base_url,
        model_name=model_name,
        api_key=encrypt(api_key),
        params=params,
        user_id=user_id,
    )
    db_session.add(model_config)
    try:
        await db_session.commit()
        await db_session.refresh(model_config)
    except Exception:
        await db_session.rollback()
        raise
    return model_config


async def update_model_config(
    db_session: AsyncSession,
    id: int,
    name: str | None,
    base_url: str,
    model_name: str | None,
    api_key: str | None,
    params: dict | None,
) -> None:
    """修改模型配置"""
    stmt = select(ModelConfig).where(ModelConfig.id == id)
    result = await db_session.execute(stmt)
    model_config = result.scalar_one_or_none()
    if not model_config:
        raise ModelConfigNotFoundError  # 模型配置不存在
    model_config.name = name or model_name or f"Model Config {random.randint(0, 1000)}"
    model_config.base_url = base_url
    model_config.model_name = model_name
    model_config.api_key = encrypt(api_key)
    model_config.params = params
    try:
        await db_session.commit()
    except Exception:
        await db_session.rollback()
        raise


async def delete_model_configs(db_session: AsyncSession, ids: list[int]) -> None:
    """批量删除模型配置"""
    stmt = select(ModelConfig).where(ModelConfig.id.in_(ids))
    result = await db_session.execute(stmt)
    model_configs = result.scalars().all()
    if not model_configs:
        raise ModelConfigNotFoundError  # 模型配置不存在
    for model_config in model_configs:
        await db_session.delete(model_config)
    try:
        await db_session.commit()
    except Exception:
        await db_session.rollback()
        raise
