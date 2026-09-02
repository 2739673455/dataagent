"""查询经验 Elasticsearch 投影同步。"""

from uuid import UUID

from app.query.models.experience import QUERY_EXPERIENCE_PURPOSE_LIMIT, QueryExperience
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.shared.clients.embedding_client_manager import EmbeddingClient

_INDEX_TEXT_MAX_CHARS = 8000


class QueryExperienceIndexer:
    """同步查询经验的当前 Elasticsearch 投影。"""

    def __init__(
        self,
        repo: QueryExperiencePGRepo,
        index_repo: QueryExperienceESRepo,
        embedding_client: EmbeddingClient,
    ) -> None:
        """绑定查询经验事实、索引和向量生成依赖。"""
        self._repo = repo
        self._index_repo = index_repo
        self._embedding_client = embedding_client

    async def sync(self, experience_id: UUID, requested_revision: int) -> int:
        """幂等同步一条查询经验的当前索引投影。"""
        async with self._repo.session.begin():
            experience = await self._repo.get(experience_id)
        if experience is None:
            await self._index_repo.delete(
                experience_id,
                revision=requested_revision,
            )
            return requested_revision
        if experience.indexed_revision >= experience.revision:
            return experience.indexed_revision

        revision = experience.revision
        if experience.status == "deleting":
            await self._index_repo.delete(experience.id, revision=revision)
            async with self._repo.session.begin():
                await self._repo.finalize_deletion(experience.id, revision)
            return revision
        if experience.status == "disabled":
            await self._index_repo.delete(experience.id, revision=revision)
        else:
            text = self._experience_text(experience)
            embedding = (await self._embedding_client.aembed_documents([text]))[0]
            await self._index_repo.index(
                experience.id,
                revision=revision,
                role_name=experience.role_name,
                authorization_epoch=experience.authorization_epoch,
                text=text,
                embedding=embedding,
            )

        async with self._repo.session.begin():
            await self._repo.mark_indexes_synced({experience.id: revision})
        return revision

    @staticmethod
    def _experience_text(experience: QueryExperience) -> str:
        """仅使用查询目的构造经验索引文本。"""
        return "\n".join(experience.purposes[-QUERY_EXPERIENCE_PURPOSE_LIMIT:])[
            :_INDEX_TEXT_MAX_CHARS
        ]
