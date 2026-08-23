from typing import cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection

from app.shared.config import app_config


async def get_mcp_tools() -> list[BaseTool]:
    """初始化 MCP 客户端并返回所有 MCP 工具"""
    connections: dict[str, Connection] = {
        name: cast(
            Connection,
            mcp_cfg.model_dump(exclude_none=True),
        )
        for name, mcp_cfg in app_config.cfg.mcp.items()
    }
    client = MultiServerMCPClient(connections)
    return await client.get_tools()
