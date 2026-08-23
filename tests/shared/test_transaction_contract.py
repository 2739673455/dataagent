"""真实 AsyncSession 事务边界契约测试"""

import unittest

from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.repositories.auth import AuthPGRepo


class AsyncSessionTransactionContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_repository_exposes_its_bound_session(self) -> None:
        async with AsyncSession() as session:
            self.assertIs(AuthPGRepo(session).session, session)

    async def test_success_closes_transaction_boundary(self) -> None:
        async with AsyncSession() as session:
            async with AuthPGRepo(session).session.begin():
                self.assertTrue(session.in_transaction())

            self.assertFalse(session.in_transaction())

    async def test_failure_rolls_back_transaction_boundary(self) -> None:
        async with AsyncSession() as session:
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                async with AuthPGRepo(session).session.begin():
                    self.assertTrue(session.in_transaction())
                    raise RuntimeError("write failed")

            self.assertFalse(session.in_transaction())

    async def test_existing_transaction_rejects_new_boundary(self) -> None:
        async with AsyncSession() as session:
            await session.begin()

            with self.assertRaises(InvalidRequestError):
                async with AuthPGRepo(session).session.begin():
                    pass

            await session.rollback()
