"""PostgreSQL 会话目录数据访问"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from langgraph.store.base import BaseStore

from app.analytics.models import ConversationInfo

_CONVERSATION_NAMESPACE = "conversations"
_SEARCH_BATCH_SIZE = 1_000


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
        title_pending: bool = True,
    ) -> ConversationInfo:
        """创建会话目录信息"""
        now = datetime.now(UTC)
        conversation = ConversationInfo(
            id=uuid4(),
            user_id=user_id,
            title=title,
            title_pending=title_pending,
            is_draft=is_draft,
            create_at=now,
            update_at=now,
        )
        await self._save(conversation)
        return conversation

    async def get(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        include_deleting: bool = False,
    ) -> ConversationInfo | None:
        """获取当前用户的会话目录信息"""
        item = await self._store.aget(
            self._namespace(user_id),
            str(conversation_id),
        )
        if item is None:
            return None
        conversation = ConversationInfo.model_validate(item.value)
        if conversation.deletion_requested_at is not None and not include_deleting:
            return None
        return conversation

    async def update(
        self,
        conversation: ConversationInfo,
        *,
        title: str | None = None,
        title_pending: bool | None = None,
        is_draft: bool | None = None,
        deletion_requested_at: datetime | None = None,
    ) -> ConversationInfo:
        """更新会话目录信息和最后活动时间"""
        changes: dict[str, object] = {"update_at": datetime.now(UTC)}
        if title is not None:
            changes["title"] = title
        if title_pending is not None:
            changes["title_pending"] = title_pending
        if is_draft is not None:
            changes["is_draft"] = is_draft
        if deletion_requested_at is not None:
            changes["deletion_requested_at"] = deletion_requested_at

        updated = conversation.model_copy(update=changes)
        await self._save(updated)
        return updated

    async def claim_title_generation(
        self,
        conversation: ConversationInfo,
        *,
        title: str,
        source: str,
    ) -> ConversationInfo:
        """记录首次标题生成输入并占用生成状态"""
        now = datetime.now(UTC)
        updated = conversation.model_copy(
            update={
                "title": title,
                "title_pending": True,
                "title_source": source,
                "title_generation_requested_at": now,
                "is_draft": False,
                "update_at": now,
            }
        )
        await self._save(updated)
        return updated

    async def complete_title_generation(
        self,
        conversation: ConversationInfo,
        *,
        title: str,
    ) -> ConversationInfo:
        """完成标题生成并清理补偿输入"""
        updated = conversation.model_copy(
            update={
                "title": title,
                "title_pending": False,
                "title_source": None,
                "title_generation_requested_at": None,
                "update_at": datetime.now(UTC),
            }
        )
        await self._save(updated)
        return updated

    async def list_all_by_user(
        self,
        user_id: int,
        *,
        include_deleting: bool = False,
    ) -> list[ConversationInfo]:
        """按最后活动时间倒序获取用户的全部会话"""
        conversations: list[ConversationInfo] = []
        offset = 0
        while items := await self._store.asearch(
            self._namespace(user_id),
            limit=_SEARCH_BATCH_SIZE,
            offset=offset,
        ):
            conversations.extend(
                ConversationInfo.model_validate(item.value) for item in items
            )
            offset += len(items)
        visible = (
            conversations
            if include_deleting
            else [item for item in conversations if item.deletion_requested_at is None]
        )
        return sorted(
            visible,
            key=lambda conversation: (conversation.update_at, str(conversation.id)),
            reverse=True,
        )

    async def list_by_user(self, user_id: int) -> list[ConversationInfo]:
        """按最后活动时间倒序获取用户的正式会话"""
        return [
            conversation
            for conversation in await self.list_all_by_user(user_id)
            if not conversation.is_draft
        ]

    async def list_expired_drafts(
        self,
        cutoff: datetime,
        *,
        limit: int,
    ) -> list[ConversationInfo]:
        """跨用户列出最后活动时间已过期的草稿"""
        conversations: list[ConversationInfo] = []
        offset = 0
        while len(conversations) < limit:
            items = await self._store.asearch(
                (_CONVERSATION_NAMESPACE,),
                filter={"is_draft": True},
                limit=_SEARCH_BATCH_SIZE,
                offset=offset,
            )
            if not items:
                break
            for item in items:
                conversation = ConversationInfo.model_validate(item.value)
                if (
                    conversation.deletion_requested_at is None
                    and conversation.update_at <= cutoff
                ):
                    conversations.append(conversation)
            offset += len(items)
        return sorted(
            (conversation for conversation in conversations if conversation.is_draft),
            key=lambda conversation: (conversation.update_at, str(conversation.id)),
        )[:limit]

    async def list_pending_deletions(self, *, limit: int) -> list[ConversationInfo]:
        """跨用户列出已写入墓碑且待物理清理的会话"""
        conversations = await self._scan_all(
            limit=limit,
            predicate=lambda item: item.deletion_requested_at is not None,
        )
        return sorted(
            conversations,
            key=lambda item: (
                item.deletion_requested_at or item.update_at,
                str(item.id),
            ),
        )[:limit]

    async def list_pending_title_generations(
        self,
        cutoff: datetime,
        *,
        limit: int,
    ) -> list[ConversationInfo]:
        """跨用户列出需要重新提交的标题生成任务"""
        return await self._scan_all(
            limit=limit,
            predicate=lambda item: (
                item.deletion_requested_at is None
                and item.title_pending
                and item.title_source is not None
                and item.title_generation_requested_at is not None
                and item.title_generation_requested_at <= cutoff
            ),
        )

    async def _scan_all(
        self,
        *,
        limit: int,
        predicate: Callable[[ConversationInfo], bool],
    ) -> list[ConversationInfo]:
        """按状态扫描跨用户会话目录"""
        conversations: list[ConversationInfo] = []
        offset = 0
        while len(conversations) < limit:
            items = await self._store.asearch(
                (_CONVERSATION_NAMESPACE,),
                limit=_SEARCH_BATCH_SIZE,
                offset=offset,
            )
            if not items:
                break
            for item in items:
                conversation = ConversationInfo.model_validate(item.value)
                if predicate(conversation):
                    conversations.append(conversation)
            offset += len(items)
        return conversations

    async def delete(self, user_id: int, conversation_id: UUID) -> None:
        """删除会话目录信息"""
        await self._store.adelete(
            self._namespace(user_id),
            str(conversation_id),
        )
