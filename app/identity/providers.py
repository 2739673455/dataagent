"""身份与授权应用能力组装。"""

from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.authorization import AssetAccessPolicy, AuthorizationService
from app.shared.clients.postgres_client_manager import PostgresClientManager


async def load_asset_policy(
    postgres: PostgresClientManager, user_id: int
) -> AssetAccessPolicy:
    """在独立读取会话内取得最新授权，不把事务带入后续外部调用。"""
    async with postgres.session() as session:
        return await AuthorizationService(IdentityPGRepo(session)).get_asset_policy(
            user_id
        )
