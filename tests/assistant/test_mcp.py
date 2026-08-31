"""MCP 连接配置边界测试。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from app.assistant.agents import mcp
from app.shared.config.app_config import StreamableHttpMCPCfg


class MCPConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_connection_credentials_are_unwrapped_only_for_client(self) -> None:
        config = StreamableHttpMCPCfg(
            transport="streamable_http",
            url=SecretStr("https://mcp.example.test/?api_key=secret"),
            headers={"Authorization": SecretStr("Bearer secret")},
        )
        client = AsyncMock()
        client.get_tools.return_value = []
        with (
            patch.object(
                mcp.app_config, "cfg", SimpleNamespace(mcp={"search": config})
            ),
            patch.object(
                mcp, "MultiServerMCPClient", return_value=client
            ) as client_class,
        ):
            tools = await mcp.get_mcp_tools()

        connection = client_class.call_args.args[0]["search"]
        self.assertIsInstance(connection["url"], str)
        self.assertIsInstance(connection["headers"]["Authorization"], str)
        self.assertEqual(tools, [])


if __name__ == "__main__":
    unittest.main()
