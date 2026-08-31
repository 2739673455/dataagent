import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import SecretStr

from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from app.shared.config.app_config import DBConfig


class DorisQueryClientRegistryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.endpoint = DBConfig(
            host="doris.internal",
            port=9030,
            user="dataagent_admin",
            password=SecretStr("admin_password"),
            database="ecommerce",
        )

    async def test_dynamic_identity_reuses_endpoint_with_query_credentials(
        self,
    ) -> None:
        manager = MagicMock()
        manager.close = AsyncMock()
        with patch(
            "app.shared.clients.doris_client_manager.DorisClientManager",
            return_value=manager,
        ) as manager_type:
            registry = DorisQueryClientRegistry(self.endpoint)
            selected = await registry.get_or_create(
                "sales",
                "sales_query",
                "query_password",
            )
            repeated = await registry.get_or_create(
                "sales",
                "sales_query",
                "query_password",
            )

        connection_config = manager_type.call_args.args[0]
        self.assertEqual(connection_config.host, self.endpoint.host)
        self.assertEqual(connection_config.database, self.endpoint.database)
        self.assertEqual(connection_config.user, "sales_query")
        self.assertEqual(
            connection_config.password.get_secret_value(),
            "query_password",
        )
        self.assertIs(selected, manager)
        self.assertIs(repeated, manager)
        manager.init.assert_called_once_with()

        await registry.close()
        manager.close.assert_awaited_once_with()

    async def test_changed_credentials_replace_and_close_stale_pool(self) -> None:
        first = MagicMock()
        first.close = AsyncMock()
        second = MagicMock()
        second.close = AsyncMock()
        with patch(
            "app.shared.clients.doris_client_manager.DorisClientManager",
            side_effect=[first, second],
        ):
            registry = DorisQueryClientRegistry(self.endpoint)
            await registry.get_or_create("sales", "sales_query", "old")
            selected = await registry.get_or_create("sales", "sales_query", "new")

        self.assertIs(selected, second)
        first.close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
