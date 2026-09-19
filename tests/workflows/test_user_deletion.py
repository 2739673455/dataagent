"""注销入口、调度、状态保护和清理重试回归，不连接外部服务。"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.dialects import postgresql

from app.identity import errors
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.shared.config.app_config import cfg
from app.shared.tasks.celery_app import celery_app
from app.workflows import tasks
from app.workflows.user_deletion import UserDeletionService


class DeletionSubmissionTest(unittest.IsolatedAsyncioTestCase):
    async def test_publish_only_after_request_has_committed(self):
        events = []

        async def request(*args):
            events.append("committed")
            return True

        def publish(user_id):
            self.assertEqual(events, ["committed"])
            self.assertEqual(user_id, 7)
            events.append("published")

        store = MagicMock(request=AsyncMock(side_effect=request))
        service = UserDeletionService(store, MagicMock(), MagicMock())
        with patch(
            "app.workflows.user_deletion.enqueue_user_deletion", side_effect=publish
        ):
            self.assertTrue(await service.request_deletion(7, operator_id=1))
        self.assertEqual(events, ["committed", "published"])

    async def test_publish_failure_keeps_request_accepted_for_recovery(self):
        store = MagicMock(request=AsyncMock(return_value=True))
        service = UserDeletionService(store, MagicMock(), MagicMock())
        with patch(
            "app.workflows.user_deletion.enqueue_user_deletion",
            side_effect=OSError("broker offline"),
        ) as publish:
            self.assertTrue(await service.request_deletion(7, operator_id=1))
        publish.assert_called_once_with(7)
        store.request.assert_awaited_once()
        store.record_failure.assert_not_called()

    async def test_duplicate_or_failed_request_does_not_publish(self):
        store = MagicMock(request=AsyncMock(return_value=False))
        service = UserDeletionService(store, MagicMock(), MagicMock())
        with patch("app.workflows.user_deletion.enqueue_user_deletion") as publish:
            self.assertFalse(await service.request_deletion(7, operator_id=1))
            store.request.side_effect = OSError("transaction failed")
            with self.assertRaises(OSError):
                await service.request_deletion(7, operator_id=1)
        publish.assert_not_called()


class DeletionWorkerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.locked = False
        self.pending = True
        self.events = []
        self.failure = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

        @asynccontextmanager
        async def lock(user_id):
            if self.locked:
                yield False
                return
            self.locked = True
            try:
                yield True
            finally:
                self.locked = False

        async def complete(*args):
            self.assertTrue(self.locked)
            self.events.append("complete")
            self.pending = False

        async def conversations(user_id):
            self.assertTrue(self.locked)
            self.events.append("conversations")
            self.started.set()
            if self.block:
                await self.release.wait()

        async def sandbox(user_id):
            self.events.append("sandbox")
            if self.failure:
                raise self.failure

        async def record(*args, **kwargs):
            self.assertTrue(self.locked)
            self.events.append("failure")

        self.store = MagicMock(
            execution_lock=lock,
            extend_claim=AsyncMock(side_effect=lambda *a, **k: self.pending),
            complete=AsyncMock(side_effect=complete),
            record_failure=AsyncMock(side_effect=record),
        )
        self.resources = SimpleNamespace(
            conversations=MagicMock(
                delete_user_conversations=AsyncMock(side_effect=conversations)
            ),
            sandbox=MagicMock(delete_user_sandbox=AsyncMock(side_effect=sandbox)),
        )
        self.init_error = None

        @asynccontextmanager
        async def resources():
            self.assertTrue(self.locked)
            self.events.append("init")
            if self.init_error:
                raise self.init_error
            try:
                yield self.resources
            finally:
                self.events.append("close")

        self.db = MagicMock(close=AsyncMock())
        for target, value in (
            ("PostgresClientManager", self.db),
            ("PostgresUserDeletionStateStore", self.store),
        ):
            patcher = patch.object(tasks, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(
            tasks, "conversation_lifecycle_resources", side_effect=resources
        )
        self.resource_factory = patcher.start()
        self.addCleanup(patcher.stop)

    async def test_success_order_and_completed_or_missing_task_skips_resources(self):
        self.assertTrue(await tasks._process_user_deletion(1))
        self.assertEqual(
            self.events, ["init", "conversations", "sandbox", "complete", "close"]
        )
        self.assertFalse(await tasks._process_user_deletion(1))
        self.resource_factory.assert_called_once()
        self.store.record_failure.assert_not_awaited()
        self.assertFalse(self.locked)

    async def test_duplicate_delivery_does_not_run_concurrently(self):
        self.block = True
        first = asyncio.create_task(tasks._process_user_deletion(1))
        try:
            await self.started.wait()
            self.assertFalse(await tasks._process_user_deletion(1))
            self.store.extend_claim.assert_awaited_once()
            self.resource_factory.assert_called_once()
        finally:
            self.release.set()
            await first
        self.assertFalse(self.locked)

    async def test_partial_cleanup_failure_can_retry_and_complete(self):
        self.failure = OSError("volume busy")
        with self.assertRaises(OSError):
            await tasks._process_user_deletion(1)
        self.assertTrue(self.pending)
        self.store.complete.assert_not_awaited()
        self.failure = None
        self.assertTrue(await tasks._process_user_deletion(1))
        self.assertFalse(self.pending)
        self.assertEqual(self.events.count("conversations"), 2)
        self.store.record_failure.assert_awaited_once()

    async def test_initialization_and_state_read_failures_are_recorded(self):
        self.init_error = ValueError("sandbox init")
        with self.assertRaises(ValueError):
            await tasks._process_user_deletion(1)
        self.store.record_failure.assert_awaited_once()
        self.store.complete.assert_not_awaited()
        self.store.record_failure.reset_mock()
        self.store.extend_claim.side_effect = RuntimeError("state read")
        with self.assertRaises(RuntimeError):
            await tasks._process_user_deletion(1)
        self.store.record_failure.assert_awaited_once()

    async def test_failure_write_preserves_original_error_and_retry_time(self):
        self.failure = ValueError("cleanup")
        self.store.record_failure.side_effect = OSError("auth unavailable")
        before = datetime.now(UTC)
        with self.assertRaises(ValueError) as raised:
            await tasks._process_user_deletion(1)
        self.assertIs(raised.exception, self.failure)
        args = self.store.record_failure.call_args.kwargs
        self.assertEqual(args["error"], "ValueError: cleanup")
        self.assertGreaterEqual(
            args["next_attempt_at"],
            before + timedelta(seconds=cfg.lifecycle.user_deletion_retry_seconds),
        )
        self.assertFalse(self.locked)

    async def test_cancellation_releases_lock_without_completing_or_resetting_lease(
        self,
    ):
        self.block = True
        worker = asyncio.create_task(tasks._process_user_deletion(1))
        await self.started.wait()
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker
        self.assertFalse(self.locked)
        self.store.complete.assert_not_awaited()
        self.store.record_failure.assert_not_awaited()
        self.assertIn("close", self.events)


class DeletionDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_publish_and_failure_write_errors_do_not_abort_batch(self):
        store = MagicMock(
            claim_due_user_ids=AsyncMock(return_value=[1, 2, 3]),
            record_failure=AsyncMock(side_effect=OSError("auth unavailable")),
        )
        db = MagicMock(close=AsyncMock())
        before = datetime.now(UTC)
        with (
            patch.object(tasks, "PostgresClientManager", return_value=db),
            patch.object(tasks, "PostgresUserDeletionStateStore", return_value=store),
            patch.object(
                tasks,
                "enqueue_user_deletion",
                side_effect=[OSError("broker"), None, None],
            ) as enqueue,
        ):
            self.assertEqual(await tasks._dispatch_due_user_deletions(), 2)
        self.assertEqual([call.args[0] for call in enqueue.call_args_list], [1, 2, 3])
        lease = store.claim_due_user_ids.call_args.kwargs["lease_until"]
        self.assertGreaterEqual(
            lease, before + timedelta(seconds=tasks.USER_DELETION_CLAIM_SECONDS)
        )
        store.record_failure.assert_awaited_once()
        db.close.assert_awaited_once()

    def test_scan_schedule_is_separate_and_task_has_no_automatic_retry(self):
        self.assertEqual(
            celery_app.conf.beat_schedule["user-deletion-recovery"]["schedule"],
            cfg.lifecycle.user_deletion_schedule_seconds,
        )
        self.assertFalse(getattr(tasks.delete_user_task, "autoretry_for", ()))


class DeletionStateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.active = False

        @asynccontextmanager
        async def transaction():
            self.active = True
            try:
                yield
            finally:
                self.active = False

        self.session = MagicMock(begin=transaction, scalar=AsyncMock(return_value=True))

        @asynccontextmanager
        async def session():
            yield self.session

        self.store = PostgresUserDeletionStateStore(MagicMock(session=session))
        self.user = SimpleNamespace(id=1, is_active=True, is_admin=False)
        self.task = SimpleNamespace(status="pending")
        self.repo = MagicMock(
            lock_security_mutation=AsyncMock(),
            get_user_by_id_for_update=AsyncMock(return_value=self.user),
            get_user_deletion_task_for_update=AsyncMock(return_value=None),
            set_user_active=AsyncMock(),
            revoke_user_refresh_tokens=AsyncMock(),
            enqueue_user_deletion=AsyncMock(),
            count_admins=AsyncMock(return_value=1),
            delete_user=AsyncMock(),
            complete_user_deletion=AsyncMock(),
            record_user_deletion_failure=AsyncMock(),
        )
        patcher = patch(
            "app.identity.services.user_deletion_store.IdentityPGRepo",
            return_value=self.repo,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_first_request_disables_revokes_and_enqueues_in_transaction(self):
        async def check(*args):
            self.assertTrue(self.active)

        self.repo.set_user_active.side_effect = check
        self.repo.revoke_user_refresh_tokens.side_effect = check
        self.repo.enqueue_user_deletion.side_effect = check
        now = datetime.now(UTC)
        self.assertTrue(await self.store.request(1, now))
        self.repo.set_user_active.assert_awaited_once_with(self.user, False)
        self.repo.revoke_user_refresh_tokens.assert_awaited_once_with(1, now)
        self.repo.enqueue_user_deletion.assert_awaited_once_with(1, now)
        self.assertFalse(self.active)

    async def test_repeat_request_preserves_pending_task(self):
        self.repo.get_user_deletion_task_for_update.return_value = self.task
        self.assertFalse(await self.store.request(1, datetime.now(UTC)))
        self.repo.enqueue_user_deletion.assert_not_awaited()
        self.repo.set_user_active.assert_not_awaited()

    async def test_completed_request_without_user_is_noop_and_unknown_user_rejected(
        self,
    ):
        self.repo.get_user_by_id_for_update.return_value = None
        self.repo.get_user_deletion_task_for_update.return_value = SimpleNamespace(
            status="completed"
        )
        self.assertFalse(await self.store.request(1, datetime.now(UTC)))
        self.repo.get_user_deletion_task_for_update.return_value = None
        with self.assertRaises(errors.UserNotFoundError):
            await self.store.request(1, datetime.now(UTC))

    async def test_last_admin_and_current_operator_are_protected(self):
        self.user.is_admin = True
        with self.assertRaises(errors.LastAdministratorError):
            await self.store.request(1, datetime.now(UTC))
        service = UserDeletionService(self.store, MagicMock(), MagicMock())
        with self.assertRaises(errors.InvalidUserMutationError):
            await service.request_deletion(1, operator_id=1)
        self.repo.enqueue_user_deletion.assert_not_awaited()

    async def test_completed_state_ignores_late_failure_and_duplicate_completion(self):
        self.repo.get_user_deletion_task_for_update.return_value = SimpleNamespace(
            status="completed"
        )
        await self.store.record_failure(
            1, error="late", next_attempt_at=datetime.now(UTC)
        )
        await self.store.complete(1, datetime.now(UTC))
        self.repo.record_user_deletion_failure.assert_not_awaited()
        self.repo.delete_user.assert_not_awaited()
        self.repo.complete_user_deletion.assert_not_awaited()

    async def test_complete_deletes_user_and_sets_terminal_state_in_one_transaction(
        self,
    ):
        self.repo.get_user_deletion_task_for_update.return_value = self.task

        async def check(*args):
            self.assertTrue(self.active)

        self.repo.delete_user.side_effect = check
        self.repo.complete_user_deletion.side_effect = check
        now = datetime.now(UTC)
        await self.store.complete(1, now)
        self.repo.delete_user.assert_awaited_once_with(self.user)
        self.repo.complete_user_deletion.assert_awaited_once_with(self.task, now)

    async def test_execution_lock_is_nonblocking_and_transaction_scoped(self):
        for acquired in (True, False):
            self.session.scalar.return_value = acquired
            async with self.store.execution_lock(7) as actual:
                self.assertEqual(actual, acquired)
                self.assertTrue(self.active)
            self.assertFalse(self.active)
        query, parameters = self.session.scalar.call_args.args
        self.assertIn("pg_try_advisory_xact_lock", str(query))
        self.assertEqual(parameters["user_id"], 7)


class DeletionRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_insert_does_not_update_existing_lease(self):
        session = MagicMock(execute=AsyncMock())
        await IdentityPGRepo(session).enqueue_user_deletion(1, datetime.now(UTC))
        sql = str(
            session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
        )
        self.assertIn("ON CONFLICT (user_id) DO NOTHING", sql)

    async def test_claim_skips_locked_and_only_changes_lease(self):
        now = datetime.now(UTC)
        lease = now + timedelta(seconds=100)
        task = SimpleNamespace(
            user_id=1, next_attempt_at=now, attempt_count=2, status="pending"
        )
        session = MagicMock(scalars=AsyncMock(return_value=[task]), flush=AsyncMock())
        actual = await IdentityPGRepo(session).claim_due_user_deletions(
            now, lease_until=lease, limit=10
        )
        self.assertEqual(actual, [task])
        self.assertEqual(task.next_attempt_at, lease)
        self.assertEqual(task.attempt_count, 2)
        statement = session.scalars.call_args.args[0].compile(
            dialect=postgresql.dialect()
        )
        self.assertIn("FOR UPDATE SKIP LOCKED", str(statement))
        self.assertIn("next_attempt_at <=", str(statement))
        self.assertIn("pending", statement.params.values())

    async def test_extend_rejects_missing_and_completed_and_renews_pending(self):
        for task in (
            None,
            SimpleNamespace(status="completed"),
            SimpleNamespace(status="pending"),
        ):
            session = MagicMock(get=AsyncMock(return_value=task), flush=AsyncMock())
            lease = datetime.now(UTC) + timedelta(seconds=100)
            actual = await IdentityPGRepo(session).extend_user_deletion_claim(
                1, lease_until=lease
            )
            self.assertEqual(actual, task is not None and task.status == "pending")
            if actual:
                assert task is not None
                self.assertEqual(task.next_attempt_at, lease)
            else:
                session.flush.assert_not_awaited()
