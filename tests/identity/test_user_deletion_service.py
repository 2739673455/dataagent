"""跨存储用户注销编排测试"""

import unittest
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from app.identity import errors as auth_error
from app.identity.repositories.auth import AuthPGRepo
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.shared.config.app_config import LifecycleConfig
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
        cleanup_interval_seconds=30,
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
        state_store.list_due_user_ids = AsyncMock(return_value=[])
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
        repo.get_user_deletion_task = AsyncMock(return_value=None)
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
        self.assertTrue(submitted)

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
        service, state_store, conversations, sandbox = (
            self.build_service()
        )

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


if __name__ == "__main__":
    unittest.main()
