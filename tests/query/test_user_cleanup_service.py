"""用户查询历史清理服务测试"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.query.services.user_cleanup import QueryHistoryCleanupService


class AsyncContextStub:
    """异步上下文测试替身"""

    def __init__(self, value: object | None = None) -> None:
        """保存进入上下文时返回的值"""
        self.value = value

    async def __aenter__(self) -> object | None:
        """进入异步上下文"""
        return self.value

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        """退出异步上下文"""
        return


class QueryHistoryCleanupServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证查询索引与数据库记录的清理顺序"""

    async def test_delete_user_query_history_cleans_index_then_database(
        self,
    ) -> None:
        """先读取经验主键，再删除索引和数据库记录"""
        postgres = MagicMock()
        read_session = MagicMock()
        delete_session = MagicMock()
        read_session.begin.return_value = AsyncContextStub()
        delete_session.begin.return_value = AsyncContextStub()
        postgres.session.side_effect = [
            AsyncContextStub(read_session),
            AsyncContextStub(delete_session),
        ]
        es = MagicMock()
        es_client = MagicMock()
        es.get_client.return_value = es_client
        experience_ids = [uuid4(), uuid4()]
        read_repo = MagicMock()
        read_repo.list_ids_by_user = AsyncMock(return_value=experience_ids)
        delete_repo = MagicMock()
        delete_repo.delete_by_user = AsyncMock()
        index_repo = MagicMock()
        index_repo.delete_many = AsyncMock()

        with (
            patch(
                "app.query.services.user_cleanup.QueryExperiencePGRepo",
                side_effect=[read_repo, delete_repo],
            ),
            patch(
                "app.query.services.user_cleanup.QueryExperienceESRepo",
                return_value=index_repo,
            ),
        ):
            await QueryHistoryCleanupService(
                postgres,
                es,
            ).delete_user_query_history(7)

        read_repo.list_ids_by_user.assert_awaited_once_with(7)
        index_repo.delete_many.assert_awaited_once_with(experience_ids)
        delete_repo.delete_by_user.assert_awaited_once_with(7)


if __name__ == "__main__":
    unittest.main()
