"""语义召回快照 PostgreSQL 数据访问"""

from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.metadata.models.recall import (
    SemanticRecallRecord,
    SemanticRecallSnapshot,
)
from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
)
from app.shared.contracts.query_experience import QueryExperienceRecallResult


class SemanticRecallPGRepo:
    """按用户和会话隔离语义召回快照"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化召回快照数据访问"""
        self._session = session

    @staticmethod
    def _to_record(snapshot: SemanticRecallSnapshot) -> SemanticRecallRecord:
        """将关系模型转换为领域记录"""
        response_payload = snapshot.response
        semantic_resources = {
            **response_payload["semantic_resources"],
            "recall_id": snapshot.recall_id,
        }
        return SemanticRecallRecord(
            user_id=snapshot.user_id,
            conversation_id=snapshot.conversation_id,
            query=snapshot.query,
            request=(
                SemanticResourceRecallRequest.model_validate(snapshot.request)
                if snapshot.request is not None
                else None
            ),
            response=SemanticResourceRecallResponse.model_validate(semantic_resources),
            query_experiences=[
                QueryExperienceRecallResult.model_validate(item)
                for item in response_payload["query_experiences"]
            ],
            query_experiences_retrieved_at=response_payload[
                "query_experiences_retrieved_at"
            ],
            query_experience_role_name=response_payload[
                "query_experience_role_name"
            ],
            query_experience_authorization_epoch=response_payload[
                "query_experience_authorization_epoch"
            ],
            source_queries=snapshot.source_queries,
            created_at=snapshot.created_at,
        )

    async def save(self, record: SemanticRecallRecord) -> None:
        """保存召回快照"""
        self._session.add(
            SemanticRecallSnapshot(
                user_id=record.user_id,
                conversation_id=record.conversation_id,
                recall_id=record.response.recall_id,
                query=record.query,
                request=(
                    record.request.model_dump(mode="json")
                    if record.request is not None
                    else None
                ),
                response={
                    "semantic_resources": record.response.model_dump(
                        mode="json",
                        exclude={"recall_id"},
                    ),
                    "query_experiences": [
                        item.model_dump(mode="json")
                        for item in record.query_experiences
                    ],
                    "query_experiences_retrieved_at": (
                        record.query_experiences_retrieved_at.isoformat()
                    ),
                    "query_experience_role_name": (
                        record.query_experience_role_name
                    ),
                    "query_experience_authorization_epoch": (
                        str(record.query_experience_authorization_epoch)
                        if record.query_experience_authorization_epoch is not None
                        else None
                    ),
                },
                source_queries=record.source_queries,
                created_at=record.created_at,
            )
        )
        await self._session.flush()

    async def acquire_query_lock(
        self,
        user_id: int,
        conversation_id: UUID,
        query: str,
    ) -> None:
        """在当前事务内锁定一个会话查询的持续上下文"""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {
                "lock_key": (
                    f"semantic-recall:{user_id}:{conversation_id}:{query}"
                )
            },
        )

    async def get_latest_by_query(
        self,
        user_id: int,
        conversation_id: UUID,
        query: str,
    ) -> SemanticRecallRecord | None:
        """获取会话中指定 query 的最新召回快照"""
        snapshot = await self._session.scalar(
            select(SemanticRecallSnapshot)
            .where(
                SemanticRecallSnapshot.user_id == user_id,
                SemanticRecallSnapshot.conversation_id == conversation_id,
                SemanticRecallSnapshot.query == query,
            )
            .order_by(
                SemanticRecallSnapshot.created_at.desc(),
                SemanticRecallSnapshot.recall_id.desc(),
            )
            .limit(1)
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
        """按创建时间倒序列出会话中每个 query 的最新快照"""
        latest = (
            select(SemanticRecallSnapshot)
            .where(
                SemanticRecallSnapshot.user_id == user_id,
                SemanticRecallSnapshot.conversation_id == conversation_id,
            )
            .distinct(SemanticRecallSnapshot.query)
            .order_by(
                SemanticRecallSnapshot.query,
                SemanticRecallSnapshot.created_at.desc(),
                SemanticRecallSnapshot.recall_id.desc(),
            )
            .subquery()
        )
        latest_snapshot = aliased(SemanticRecallSnapshot, latest)
        snapshots = (
            await self._session.scalars(
                select(latest_snapshot)
                .order_by(
                    latest_snapshot.created_at.desc(),
                    latest_snapshot.recall_id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [self._to_record(snapshot) for snapshot in snapshots]

    async def delete_by_query(
        self,
        user_id: int,
        conversation_id: UUID,
        query: str,
    ) -> bool:
        """删除 query 的全部召回快照并返回删除前是否存在"""
        snapshot = await self._session.scalar(
            select(SemanticRecallSnapshot).where(
                SemanticRecallSnapshot.user_id == user_id,
                SemanticRecallSnapshot.conversation_id == conversation_id,
                SemanticRecallSnapshot.query == query,
            )
        )
        if snapshot is None:
            return False
        await self._session.execute(
            delete(SemanticRecallSnapshot).where(
                SemanticRecallSnapshot.user_id == user_id,
                SemanticRecallSnapshot.conversation_id == conversation_id,
                SemanticRecallSnapshot.query == query,
            )
        )
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
