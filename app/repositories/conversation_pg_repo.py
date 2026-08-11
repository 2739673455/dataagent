"""PostgreSQL 会话目录数据访问"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from langgraph.store.base import BaseStore

from app.entities.conversation import ConversationInfo

_CONVERSATION_NAMESPACE = "conversations"
_MAX_CONVERSATIONS_PER_USER = 10_000


class ConversationPGRepo:
    """使用 LangGraph PostgreSQL Store 存储会话目录"""

    def __init__(self, store: BaseStore) -> None:
        """初始化会话目录数据访问"""
        self._store = store

    @staticmethod
    def _namespace(user_id: int) -> tuple[str, str]:
        """构造用户级会话命名空间"""
        return (_CONVERSATION_NAMESPACE, str(user_id))

    async def _save(self, conversation: ConversationInfo) -> None:
        """保存会话目录信息"""
        await self._store.aput(
            self._namespace(conversation.user_id),
            str(conversation.id),
            conversation.model_dump(mode="json"),
            index=False,
        )

    async def create(
        self,
        user_id: int,
        title: str,
        *,
        is_draft: bool = False,
    ) -> ConversationInfo:
        """创建会话目录信息"""
        now = datetime.now(UTC)
        conversation = ConversationInfo(
            id=uuid4(),
            user_id=user_id,
            title=title,
            is_draft=is_draft,
            create_at=now,
            update_at=now,
        )
        await self._save(conversation)
        return conversation

    async def get(self, user_id: int, conversation_id: UUID) -> ConversationInfo | None:
        """获取当前用户的会话目录信息"""
        item = await self._store.aget(
            self._namespace(user_id),
            str(conversation_id),
        )
        if item is None:
            return None
        return ConversationInfo.model_validate(item.value)

    async def update(
        self,
        conversation: ConversationInfo,
        *,
        title: str | None = None,
        is_draft: bool | None = None,
    ) -> ConversationInfo:
        """更新会话目录信息和最后活动时间"""
        changes: dict[str, object] = {"update_at": datetime.now(UTC)}
        if title is not None:
            changes["title"] = title
        if is_draft is not None:
            changes["is_draft"] = is_draft

        updated = conversation.model_copy(update=changes)
        await self._save(updated)
        return updated

    async def list_by_user(self, user_id: int) -> list[ConversationInfo]:
        """按最后活动时间倒序获取用户的正式会话"""
        items = await self._store.asearch(
            self._namespace(user_id),
            limit=_MAX_CONVERSATIONS_PER_USER,
        )
        conversations = [
            ConversationInfo.model_validate(item.value)
            for item in items
            if not item.value.get("is_draft", False)
        ]
        return sorted(
            conversations,
            key=lambda conversation: (conversation.update_at, str(conversation.id)),
            reverse=True,
        )

    async def delete(self, user_id: int, conversation_id: UUID) -> None:
        """删除会话目录信息"""
        await self._store.adelete(
            self._namespace(user_id),
            str(conversation_id),
        )
