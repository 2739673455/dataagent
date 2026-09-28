from typing import cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection
from pydantic import SecretStr

from app.shared.config import app_config


async def get_mcp_tools() -> list[BaseTool]:
    """初始化 MCP 客户端并返回所有 MCP 工具。"""
    connections: dict[str, Connection] = {}
    for name, mcp_cfg in app_config.cfg.mcp.items():
        connection = mcp_cfg.model_dump(exclude_none=True)
        url = connection.get("url")
        if isinstance(url, SecretStr):
            connection["url"] = url.get_secret_value()
        for field_name in ("headers", "env"):
            values = connection.get(field_name)
            if isinstance(values, dict):
                connection[field_name] = {
                    key: value.get_secret_value()
                    if isinstance(value, SecretStr)
                    else value
                    for key, value in values.items()
                }
        connections[name] = cast(Connection, connection)
    client = MultiServerMCPClient(connections)
    return await client.get_tools()
