"""语义召回记录 PostgreSQL Store 数据访问"""

from uuid import UUID

from langgraph.store.base import BaseStore

from app.metadata.recall_models import SemanticRecallRecord

_SEMANTIC_RECALL_NAMESPACE = "semantic_recalls"
_DELETE_BATCH_SIZE = 1_000


class SemanticRecallPGRepo:
    """按用户和会话隔离语义召回记录"""

    def __init__(self, store: BaseStore) -> None:
        """初始化召回记录数据访问"""
        self._store = store

    @staticmethod
    def _namespace(user_id: int, conversation_id: UUID) -> tuple[str, str, str]:
        """构造会话级召回记录命名空间"""
        return (
            _SEMANTIC_RECALL_NAMESPACE,
            str(user_id),
            str(conversation_id),
        )

    async def save(self, record: SemanticRecallRecord) -> None:
        """保存召回记录快照"""
        await self._store.aput(
            self._namespace(record.user_id, record.conversation_id),
            record.recall_id,
            record.model_dump(mode="json"),
            index=False,
        )

    async def get(
        self,
        user_id: int,
        conversation_id: UUID,
        recall_id: str,
    ) -> SemanticRecallRecord | None:
        """获取指定召回记录"""
        item = await self._store.aget(
            self._namespace(user_id, conversation_id),
            recall_id,
        )
        if item is None:
            return None
        return SemanticRecallRecord.model_validate(item.value)

    async def list(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[SemanticRecallRecord]:
        """按创建时间倒序列出会话召回记录"""
        items = await self._store.asearch(
            self._namespace(user_id, conversation_id),
            limit=limit,
            offset=offset,
        )
        records = [SemanticRecallRecord.model_validate(item.value) for item in items]
        return sorted(
            records,
            key=lambda record: (record.created_at, record.recall_id),
            reverse=True,
        )

    async def delete(
        self,
        user_id: int,
        conversation_id: UUID,
        recall_id: str,
    ) -> bool:
        """删除召回记录并返回删除前是否存在"""
        namespace = self._namespace(user_id, conversation_id)
        item = await self._store.aget(namespace, recall_id)
        if item is None:
            return False
        await self._store.adelete(namespace, recall_id)
        return True

    async def delete_all(self, user_id: int, conversation_id: UUID) -> None:
        """删除会话下的全部召回记录"""
        namespace = self._namespace(user_id, conversation_id)
        while items := await self._store.asearch(
            namespace,
            limit=_DELETE_BATCH_SIZE,
        ):
            for item in items:
                await self._store.adelete(namespace, item.key)

    async def delete_all_by_user(self, user_id: int) -> None:
        """删除用户全部会话下的召回记录"""
        namespace_prefix = (_SEMANTIC_RECALL_NAMESPACE, str(user_id))
        while items := await self._store.asearch(
            namespace_prefix,
            limit=_DELETE_BATCH_SIZE,
        ):
            for item in items:
                await self._store.adelete(item.namespace, item.key)
