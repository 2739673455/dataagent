"""组装身份仓库和服务，读取用户的资产访问策略。"""

from app.identity.models.authorization import AssetAccessPolicy
from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.identity import IdentityService
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg


async def load_asset_policy(
    postgres: PostgresClientManager, doris: DorisClientManager, user_id: int
) -> AssetAccessPolicy:
    """读取用户对应的 Doris 权限并构造资产策略。"""
    async with postgres.session() as session:
        return await IdentityService(IdentityPGRepo(session)).get_asset_policy(
            user_id,
            DorisRoleRepository(doris),
            data_source="doris",
            database=cfg.doris.database,
        )
