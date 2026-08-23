"""跨存储用户注销编排测试"""

import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from app.identity import errors as auth_error
from app.identity.repositories.auth import AuthPGRepo
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
    ) -> tuple[UserDeletionService, MagicMock, MagicMock, MagicMock]:
        auth_postgres = MagicMock()
        meta_postgres = MagicMock()
        es = MagicMock()
        sandbox = MagicMock()
        sandbox.delete_user_sandbox = AsyncMock()
        conversations = MagicMock()
        conversations.delete_user_conversations = AsyncMock()
        service = UserDeletionService(
            auth_postgres,
            meta_postgres,
            es,
            sandbox,
            conversations,
            build_config(),
        )
        return service, auth_postgres, conversations, sandbox

    async def test_request_disables_user_revokes_tokens_and_enqueues_task(self) -> None:
        service, auth_postgres, _, _ = self.build_service()
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

        with (
            patch(
                "app.workflows.user_deletion.AuthPGRepo",
                return_value=repo,
            ),
            patch.object(service, "_process", new=AsyncMock()) as process,
        ):
            await service.request_deletion(user.id, operator_id=1)

        repo.set_user_active.assert_awaited_once_with(user, False)
        repo.revoke_user_refresh_tokens.assert_awaited_once_with(
            user.id,
            ANY,
        )
        repo.enqueue_user_deletion.assert_awaited_once_with(
            user.id,
            ANY,
        )
        process.assert_awaited_once_with(user.id)

    async def test_delete_self_is_rejected_before_any_storage_access(self) -> None:
        service, auth_postgres, _, _ = self.build_service()

        with self.assertRaises(auth_error.InvalidUserMutationError):
            await service.request_deletion(1, operator_id=1)

        auth_postgres.session.assert_not_called()

    async def test_completed_deletion_can_be_submitted_again(self) -> None:
        service, auth_postgres, _, _ = self.build_service()
        session = MagicMock()
        session.begin.return_value = AsyncContextStub()
        auth_postgres.session.return_value = AsyncContextStub(session)
        repo = MagicMock(spec=AuthPGRepo)
        repo.lock_security_mutation = AsyncMock()
        repo.get_user_by_id_for_update = AsyncMock(return_value=None)
        repo.get_user_deletion_task = AsyncMock(
            return_value=MagicMock(status="completed")
        )

        with (
            patch(
                "app.workflows.user_deletion.AuthPGRepo",
                return_value=repo,
            ),
            patch.object(service, "_process", new=AsyncMock()) as process,
        ):
            await service.request_deletion(8, operator_id=1)

        process.assert_not_awaited()

    async def test_process_cleans_each_storage_then_completes_task(self) -> None:
        service, _, conversations, sandbox = self.build_service()

        with (
            patch.object(
                service,
                "_is_completed",
                new=AsyncMock(return_value=False),
            ),
            patch.object(
                service,
                "_delete_query_history",
                new=AsyncMock(),
            ) as delete_query_history,
            patch.object(
                service,
                "_complete",
                new=AsyncMock(),
            ) as complete,
            patch.object(
                service,
                "_record_failure",
                new=AsyncMock(),
            ) as record_failure,
        ):
            await service._process(8)

        conversations.delete_user_conversations.assert_awaited_once_with(8)
        sandbox.delete_user_sandbox.assert_awaited_once_with(8)
        delete_query_history.assert_awaited_once_with(8)
        complete.assert_awaited_once_with(8)
        record_failure.assert_not_awaited()
        self.assertNotIn(8, service._locks)

    async def test_process_records_failure_for_automatic_retry(self) -> None:
        service, _, conversations, sandbox = self.build_service()
        failure = RuntimeError("sandbox unavailable")
        conversations.delete_user_conversations.side_effect = failure

        with (
            patch.object(
                service,
                "_is_completed",
                new=AsyncMock(return_value=False),
            ),
            patch.object(
                service,
                "_record_failure",
                new=AsyncMock(),
            ) as record_failure,
            self.assertRaisesRegex(RuntimeError, "sandbox unavailable"),
        ):
            await service._process(8)

        record_failure.assert_awaited_once_with(8, failure)
        sandbox.delete_user_sandbox.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
