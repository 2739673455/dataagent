"""身份与授权应用能力组装。"""

from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.authorization import AssetAccessPolicy, AuthorizationService
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg


async def load_asset_policy(
    postgres: PostgresClientManager, doris: DorisClientManager, user_id: int
) -> AssetAccessPolicy:
    """一次操作读取 Doris 权限并提交版本，不把事务带入检索或模型调用。"""
    async with postgres.session() as session, session.begin():
        return await AuthorizationService(
            IdentityPGRepo(session),
            DorisRoleRepository(doris),
            data_source=cfg.query.data_source,
            database=cfg.doris.database,
        ).get_asset_policy(user_id)
