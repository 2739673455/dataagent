from typing import cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection
from pydantic import SecretStr

from app.shared.config import app_config
from app.shared.config.app_config import MCPCfg


def _connection_config(mcp_cfg: MCPCfg) -> Connection:
    """在 MCP 客户端边界解包连接凭据。"""
    connection = mcp_cfg.model_dump(exclude_none=True)
    url = connection.get("url")
    if isinstance(url, SecretStr):
        connection["url"] = url.get_secret_value()
    for field_name in ("headers", "env"):
        values = connection.get(field_name)
        if isinstance(values, dict):
            connection[field_name] = {
                name: value.get_secret_value()
                if isinstance(value, SecretStr)
                else value
                for name, value in values.items()
            }
    return cast(Connection, connection)


async def get_mcp_tools() -> list[BaseTool]:
    """初始化 MCP 客户端并返回所有 MCP 工具。"""
    connections: dict[str, Connection] = {
        name: _connection_config(mcp_cfg)
        for name, mcp_cfg in app_config.cfg.mcp.items()
    }
    client = MultiServerMCPClient(connections)
    return await client.get_tools()
