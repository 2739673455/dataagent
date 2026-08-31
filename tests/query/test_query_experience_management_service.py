"""查询经验管理用例测试。"""

import unittest
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from app.query import errors as query_error
from app.query.models.experience import QueryExperience, QueryExperienceOverview
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.experience_management import QueryExperienceManagementService


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _Session:
    def begin(self) -> _Transaction:
        return _Transaction()


class _Scheduler:
    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, int]] = []

    def enqueue(self, experience_id: UUID, revision: int) -> None:
        self.enqueued.append((experience_id, revision))


class _Repo:
    def __init__(
        self,
        experience: QueryExperience | list[QueryExperience] | None,
    ) -> None:
        self.session = _Session()
        experiences = experience if isinstance(experience, list) else [experience]
        self.experiences = {item.id: item for item in experiences if item is not None}

    async def get(self, experience_id: UUID) -> QueryExperience | None:
        return self.experiences.get(experience_id)

    async def get_overview(
        self,
        experience_id: UUID,
    ) -> QueryExperienceOverview | None:
        experience = await self.get(experience_id)
        if experience is None:
            return None
        return QueryExperienceOverview(experience, 0, 0, None)

    async def disable_manually(
        self,
        experience_id: UUID,
        admin_user_id: int,
    ) -> tuple[QueryExperience | None, bool]:
        experience = await self.get(experience_id)
        if experience is None or experience.status == "deleting":
            return experience, False
        if experience.status == "disabled" and experience.disabled_reason == "admin":
            return experience, False
        experience.status = "disabled"
        experience.disabled_reason = "admin"
        experience.disabled_by_user_id = admin_user_id
        experience.disabled_at = datetime.now(UTC)
        experience.revision += 1
        return experience, True

    async def request_deletion(
        self,
        experience_id: UUID,
        admin_user_id: int,
    ) -> tuple[QueryExperience | None, bool]:
        experience = await self.get(experience_id)
        if experience is None:
            return None, False
        if experience.status == "deleting":
            return experience, False
        experience.status = "deleting"
        experience.disabled_reason = None
        experience.disabled_by_user_id = None
        experience.disabled_at = None
        experience.deletion_requested_by_user_id = admin_user_id
        experience.deletion_requested_at = datetime.now(UTC)
        experience.revision += 1
        return experience, True


def _experience() -> QueryExperience:
    now = datetime.now(UTC)
    experience = QueryExperience(
        id=uuid4(),
        role_name="analyst",
        authorization_epoch=uuid4(),
        fingerprint=uuid4().hex,
        purposes=["统计订单"],
        sql_template="SELECT SUM(amount) FROM orders",
        status="active",
        revision=1,
        indexed_revision=1,
        created_at=now,
        updated_at=now,
    )
    experience.assets = []
    return experience


def _service(
    repo: _Repo,
    scheduler: _Scheduler,
) -> QueryExperienceManagementService:
    return QueryExperienceManagementService(
        cast(QueryExperiencePGRepo, cast(Any, repo)),
        scheduler,
    )


class QueryExperienceManagementServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_batch_disable_updates_unique_experiences(self) -> None:
        first = _experience()
        second = _experience()
        scheduler = _Scheduler()
        service = _service(_Repo([first, second]), scheduler)

        await service.disable_experiences(
            [first.id, second.id, first.id],
            operator_id=7,
        )

        self.assertEqual(first.status, "disabled")
        self.assertEqual(second.status, "disabled")
        self.assertEqual(
            scheduler.enqueued,
            [(first.id, first.revision), (second.id, second.revision)],
        )

    async def test_batch_delete_submits_unique_experiences(self) -> None:
        first = _experience()
        second = _experience()
        scheduler = _Scheduler()
        service = _service(_Repo([first, second]), scheduler)

        await service.request_deletions(
            [first.id, second.id, first.id],
            operator_id=7,
        )

        self.assertEqual(first.status, "deleting")
        self.assertEqual(second.status, "deleting")
        self.assertEqual(
            scheduler.enqueued,
            [(first.id, first.revision), (second.id, second.revision)],
        )

    async def test_admin_disable_is_idempotent(self) -> None:
        experience = _experience()
        repo = _Repo(experience)
        scheduler = _Scheduler()
        service = _service(repo, scheduler)

        await service.disable_experience(experience.id, operator_id=7)
        revision = experience.revision
        await service.disable_experience(experience.id, operator_id=8)

        self.assertEqual(experience.status, "disabled")
        self.assertEqual(experience.disabled_reason, "admin")
        self.assertEqual(experience.disabled_by_user_id, 7)
        self.assertEqual(experience.revision, revision)
        self.assertEqual(scheduler.enqueued, [(experience.id, revision)])

    async def test_deleting_experience_cannot_be_disabled(self) -> None:
        experience = _experience()
        experience.status = "deleting"
        experience.deletion_requested_by_user_id = 7
        experience.deletion_requested_at = datetime.now(UTC)
        service = _service(_Repo(experience), _Scheduler())

        with self.assertRaises(query_error.QueryExperienceStateConflictError):
            await service.disable_experience(experience.id, operator_id=7)

    async def test_active_experience_can_be_deleted_directly(self) -> None:
        experience = _experience()
        scheduler = _Scheduler()
        service = _service(_Repo(experience), scheduler)

        result = await service.request_deletion(experience.id, operator_id=7)

        self.assertEqual(result.id, experience.id)
        self.assertEqual(experience.status, "deleting")
        self.assertEqual(experience.deletion_requested_by_user_id, 7)
        self.assertEqual(scheduler.enqueued, [(experience.id, experience.revision)])

    async def test_repeated_deletion_keeps_revision(self) -> None:
        experience = _experience()
        scheduler = _Scheduler()
        service = _service(_Repo(experience), scheduler)

        await service.request_deletion(experience.id, operator_id=7)
        revision = experience.revision
        await service.request_deletion(experience.id, operator_id=8)

        self.assertEqual(experience.revision, revision)
        self.assertEqual(experience.deletion_requested_by_user_id, 7)
        self.assertEqual(scheduler.enqueued, [(experience.id, revision)])

    async def test_missing_experience_returns_not_found(self) -> None:
        service = _service(_Repo(None), _Scheduler())

        with self.assertRaises(query_error.QueryExperienceNotFoundError):
            await service.get_overview(uuid4())
