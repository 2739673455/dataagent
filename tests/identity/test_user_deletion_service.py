"""跨存储用户注销编排测试"""

import unittest
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from app.identity import errors as auth_error
from app.identity.repositories.auth import AuthPGRepo
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.shared.config.app_config import LifecycleConfig
from app.shared.tasks.celery_app import TASK_VISIBILITY_TIMEOUT_SECONDS
from app.workflows.tasks import _dispatch_due_user_deletions
from app.workflows.user_deletion import UserDeletionService
from tests.identity.test_auth_service import build_user


class AsyncContextStub:
    def __init__(self, value: object | None = None) -> None:
        self.value = value

    async def __aenter__(self) -> object | None:
        return self.value

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None


def build_config() -> LifecycleConfig:
    return LifecycleConfig(
        draft_ttl_minutes=60,
        cleanup_batch_size=100,
        user_deletion_retry_seconds=10,
    )


class UserDeletionServiceTest(unittest.IsolatedAsyncioTestCase):
    def build_service(
        self,
    ) -> tuple[
        UserDeletionService,
        MagicMock,
        MagicMock,
        MagicMock,
    ]:
        state_store = MagicMock()
        state_store.request = AsyncMock(return_value=True)
        state_store.is_completed = AsyncMock(return_value=False)
        state_store.complete = AsyncMock()
        state_store.record_failure = AsyncMock()
        sandbox = MagicMock()
        sandbox.delete_user_sandbox = AsyncMock()
        conversations = MagicMock()
        conversations.delete_user_conversations = AsyncMock()
        service = UserDeletionService(
            state_store,
            sandbox,
            conversations,
            build_config(),
        )
        return service, state_store, conversations, sandbox

    async def test_request_disables_user_revokes_tokens_and_enqueues_task(self) -> None:
        service, state_store, _, _ = self.build_service()

        submitted = await service.request_deletion(8, operator_id=1)

        state_store.request.assert_awaited_once_with(8, ANY)
        self.assertTrue(submitted)

    async def test_state_store_disables_user_and_enqueues_task(self) -> None:
        auth_postgres = MagicMock()
        store = PostgresUserDeletionStateStore(auth_postgres)
        user = build_user(user_id=8)
        session = MagicMock()
        session.begin.return_value = AsyncContextStub()
        auth_postgres.session.return_value = AsyncContextStub(session)
        repo = MagicMock(spec=AuthPGRepo)
        repo.lock_security_mutation = AsyncMock()
        repo.get_user_by_id_for_update = AsyncMock(return_value=user)
        repo.get_user_deletion_task_for_update = AsyncMock(return_value=None)
        repo.set_user_active = AsyncMock()
        repo.revoke_user_refresh_tokens = AsyncMock()
        repo.enqueue_user_deletion = AsyncMock()
        requested_at = datetime.now(UTC)

        with patch(
            "app.identity.services.user_deletion_store.AuthPGRepo",
            return_value=repo,
        ):
            submitted = await store.request(user.id, requested_at)

        repo.set_user_active.assert_awaited_once_with(user, False)
        repo.revoke_user_refresh_tokens.assert_awaited_once_with(
            user.id,
            requested_at,
        )
        repo.enqueue_user_deletion.assert_awaited_once_with(
            user.id,
            requested_at,
        )
        repo.get_user_deletion_task_for_update.assert_awaited_once_with(user.id)
        self.assertTrue(submitted)

    async def test_repository_locks_deletion_task_before_mutation(self) -> None:
        session = MagicMock()
        task = MagicMock()
        session.get = AsyncMock(return_value=task)
        repo = AuthPGRepo(session)

        selected = await repo.get_user_deletion_task_for_update(8)

        self.assertIs(selected, task)
        session.get.assert_awaited_once_with(ANY, 8, with_for_update=True)

    async def test_late_failure_cannot_overwrite_completed_deletion(self) -> None:
        auth_postgres = MagicMock()
        store = PostgresUserDeletionStateStore(auth_postgres)
        session = MagicMock()
        session.begin.return_value = AsyncContextStub()
        auth_postgres.session.return_value = AsyncContextStub(session)
        repo = MagicMock(spec=AuthPGRepo)
        repo.get_user_deletion_task_for_update = AsyncMock(
            return_value=MagicMock(status="completed")
        )
        repo.record_user_deletion_failure = AsyncMock()

        with patch(
            "app.identity.services.user_deletion_store.AuthPGRepo",
            return_value=repo,
        ):
            await store.record_failure(
                8,
                error="RuntimeError: delayed",
                next_attempt_at=datetime.now(UTC),
            )

        repo.get_user_deletion_task_for_update.assert_awaited_once_with(8)
        repo.record_user_deletion_failure.assert_not_awaited()

    async def test_state_store_atomically_claims_due_tasks(self) -> None:
        auth_postgres = MagicMock()
        store = PostgresUserDeletionStateStore(auth_postgres)
        session = MagicMock()
        session.begin.return_value = AsyncContextStub()
        auth_postgres.session.return_value = AsyncContextStub(session)
        repo = MagicMock(spec=AuthPGRepo)
        task = MagicMock(user_id=8)
        repo.claim_due_user_deletions = AsyncMock(return_value=[task])
        claimed_at = datetime.now(UTC)
        lease_until = datetime.now(UTC)

        with patch(
            "app.identity.services.user_deletion_store.AuthPGRepo",
            return_value=repo,
        ):
            user_ids = await store.claim_due_user_ids(
                claimed_at,
                lease_until=lease_until,
                limit=100,
            )

        repo.claim_due_user_deletions.assert_awaited_once_with(
            claimed_at,
            lease_until=lease_until,
            limit=100,
        )
        self.assertEqual(user_ids, [8])

    async def test_repository_claim_sets_lease_before_returning(self) -> None:
        session = MagicMock()
        task = MagicMock(user_id=8)
        session.scalars = AsyncMock(return_value=[task])
        session.flush = AsyncMock()
        repo = AuthPGRepo(session)
        claimed_at = datetime.now(UTC)
        lease_until = datetime.now(UTC)

        tasks = await repo.claim_due_user_deletions(
            claimed_at,
            lease_until=lease_until,
            limit=100,
        )

        statement = session.scalars.await_args.args[0]
        self.assertTrue(statement._for_update_arg.skip_locked)
        self.assertEqual(task.next_attempt_at, lease_until)
        session.flush.assert_awaited_once()
        self.assertEqual(tasks, [task])

    async def test_state_store_extends_claim_transactionally(self) -> None:
        auth_postgres = MagicMock()
        store = PostgresUserDeletionStateStore(auth_postgres)
        session = MagicMock()
        session.begin.return_value = AsyncContextStub()
        auth_postgres.session.return_value = AsyncContextStub(session)
        repo = MagicMock(spec=AuthPGRepo)
        repo.extend_user_deletion_claim = AsyncMock(return_value=True)
        lease_until = datetime.now(UTC)

        with patch(
            "app.identity.services.user_deletion_store.AuthPGRepo",
            return_value=repo,
        ):
            extended = await store.extend_claim(8, lease_until=lease_until)

        self.assertTrue(extended)
        repo.extend_user_deletion_claim.assert_awaited_once_with(
            8,
            lease_until=lease_until,
        )

    async def test_delete_self_is_rejected_before_any_storage_access(self) -> None:
        service, state_store, _, _ = self.build_service()

        with self.assertRaises(auth_error.InvalidUserMutationError):
            await service.request_deletion(1, operator_id=1)

        state_store.request.assert_not_called()

    async def test_completed_deletion_can_be_submitted_again(self) -> None:
        service, state_store, _, _ = self.build_service()
        state_store.request.return_value = False

        submitted = await service.request_deletion(8, operator_id=1)

        self.assertFalse(submitted)

    async def test_process_cleans_each_storage_then_completes_task(self) -> None:
        service, state_store, conversations, sandbox = self.build_service()

        await service.process(8)

        conversations.delete_user_conversations.assert_awaited_once_with(8)
        sandbox.delete_user_sandbox.assert_awaited_once_with(8)
        state_store.complete.assert_awaited_once_with(8, ANY)
        state_store.record_failure.assert_not_awaited()

    async def test_process_records_failure_for_automatic_retry(self) -> None:
        service, state_store, conversations, sandbox = self.build_service()
        failure = RuntimeError("sandbox unavailable")
        conversations.delete_user_conversations.side_effect = failure

        with self.assertRaisesRegex(RuntimeError, "sandbox unavailable"):
            await service.process(8)

        state_store.record_failure.assert_awaited_once_with(
            8,
            error="RuntimeError: sandbox unavailable",
            next_attempt_at=ANY,
        )
        sandbox.delete_user_sandbox.assert_not_awaited()

    async def test_dispatch_claims_tasks_and_releases_publish_failures(self) -> None:
        postgres = MagicMock()
        postgres.close = AsyncMock()
        state_store = MagicMock()
        state_store.claim_due_user_ids = AsyncMock(return_value=[8, 9])
        state_store.record_failure = AsyncMock()

        with (
            patch(
                "app.workflows.tasks.PostgresClientManager",
                return_value=postgres,
            ),
            patch(
                "app.workflows.tasks.PostgresUserDeletionStateStore",
                return_value=state_store,
            ),
            patch(
                "app.workflows.tasks.enqueue_user_deletion",
                side_effect=[MagicMock(), RuntimeError("broker unavailable")],
            ),
        ):
            dispatched_count = await _dispatch_due_user_deletions()

        self.assertEqual(dispatched_count, 1)
        claim = state_store.claim_due_user_ids.await_args
        claimed_at = claim.args[0]
        lease_until = claim.kwargs["lease_until"]
        self.assertEqual(
            (lease_until - claimed_at).total_seconds(),
            TASK_VISIBILITY_TIMEOUT_SECONDS,
        )
        state_store.record_failure.assert_awaited_once_with(
            9,
            error="RuntimeError: broker unavailable",
            next_attempt_at=ANY,
        )
        postgres.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
