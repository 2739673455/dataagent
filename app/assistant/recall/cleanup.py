"""会话和用户召回记录的事务清理边界。"""

from uuid import UUID

from app.assistant.repositories.recall import SemanticRecallPGRepo
from app.shared.clients.postgres_client_manager import PostgresClientManager


class RecallCleanupService:
    """在独立短事务中删除召回记录，供跨存储清理流程调用。"""

    def __init__(self, postgres: PostgresClientManager) -> None:
        """绑定召回记录清理所需的数据库会话管理器。"""
        self._postgres = postgres

    async def delete_conversation(self, user_id: int, conversation_id: UUID) -> None:
        """提交会话全部召回记录的删除。"""
        async with self._postgres.session() as session, session.begin():
            await SemanticRecallPGRepo(session).delete_all(user_id, conversation_id)

    async def delete_user(self, user_id: int) -> None:
        """提交用户所有残留召回记录的删除。"""
        async with self._postgres.session() as session, session.begin():
            await SemanticRecallPGRepo(session).delete_all_by_user(user_id)
