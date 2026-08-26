"""语义召回快照 PostgreSQL 数据访问"""

from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.metadata.models.recall import (
    SemanticRecallKind,
    SemanticRecallRecord,
    SemanticRecallSnapshot,
)
from app.metadata.models.search import SemanticSearchRequest, SemanticSearchResponse


class SemanticRecallPGRepo:
    """按用户和会话隔离语义召回快照"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化召回快照数据访问"""
        self._session = session

    @staticmethod
    def _to_record(snapshot: SemanticRecallSnapshot) -> SemanticRecallRecord:
        """将关系模型转换为领域记录"""
        return SemanticRecallRecord(
            recall_id=snapshot.recall_id,
            user_id=snapshot.user_id,
            conversation_id=snapshot.conversation_id,
            kind=cast("SemanticRecallKind", snapshot.kind),
            request=(
                SemanticSearchRequest.model_validate(snapshot.request)
                if snapshot.request is not None
                else None
            ),
            response=SemanticSearchResponse.model_validate(snapshot.response),
            source_recall_ids=snapshot.source_recall_ids,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
        )

    async def save(self, record: SemanticRecallRecord) -> None:
        """保存召回快照"""
        self._session.add(
            SemanticRecallSnapshot(
                user_id=record.user_id,
                conversation_id=record.conversation_id,
                recall_id=record.recall_id,
                kind=record.kind,
                request=(
                    record.request.model_dump(mode="json")
                    if record.request is not None
                    else None
                ),
                response=record.response.model_dump(mode="json"),
                source_recall_ids=record.source_recall_ids,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )
        await self._session.flush()

    async def get(
        self,
        user_id: int,
        conversation_id: UUID,
        recall_id: str,
    ) -> SemanticRecallRecord | None:
        """获取指定召回快照"""
        snapshot = await self._session.scalar(
            select(SemanticRecallSnapshot).where(
                SemanticRecallSnapshot.user_id == user_id,
                SemanticRecallSnapshot.conversation_id == conversation_id,
                SemanticRecallSnapshot.recall_id == recall_id,
            )
        )
        return self._to_record(snapshot) if snapshot is not None else None

    async def list(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[SemanticRecallRecord]:
        """按创建时间倒序列出会话召回快照"""
        snapshots = (
            await self._session.scalars(
                select(SemanticRecallSnapshot)
                .where(
                    SemanticRecallSnapshot.user_id == user_id,
                    SemanticRecallSnapshot.conversation_id == conversation_id,
                )
                .order_by(
                    SemanticRecallSnapshot.created_at.desc(),
                    SemanticRecallSnapshot.recall_id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [self._to_record(snapshot) for snapshot in snapshots]

    async def delete(
        self,
        user_id: int,
        conversation_id: UUID,
        recall_id: str,
    ) -> bool:
        """删除召回快照并返回删除前是否存在"""
        snapshot = await self._session.scalar(
            select(SemanticRecallSnapshot).where(
                SemanticRecallSnapshot.user_id == user_id,
                SemanticRecallSnapshot.conversation_id == conversation_id,
                SemanticRecallSnapshot.recall_id == recall_id,
            )
        )
        if snapshot is None:
            return False
        await self._session.delete(snapshot)
        await self._session.flush()
        return True

    async def delete_all(self, user_id: int, conversation_id: UUID) -> None:
        """删除会话下的全部召回快照"""
        await self._session.execute(
            delete(SemanticRecallSnapshot).where(
                SemanticRecallSnapshot.user_id == user_id,
                SemanticRecallSnapshot.conversation_id == conversation_id,
            )
        )
        await self._session.flush()

    async def delete_all_by_user(self, user_id: int) -> None:
        """删除用户全部会话下的召回快照"""
        await self._session.execute(
            delete(SemanticRecallSnapshot).where(
                SemanticRecallSnapshot.user_id == user_id
            )
        )
        await self._session.flush()
