from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import (
    SSEConnection,
    StdioConnection,
    StreamableHttpConnection,
    WebsocketConnection,
)

from app.core import settings


async def get_mcp_tools() -> list:
    """初始化 MCP 客户端并返回所有 MCP 工具"""
    connections = {
        name: {
            "sse": SSEConnection,
            "stdio": StdioConnection,
            "websocket": WebsocketConnection,
            "streamable_http": StreamableHttpConnection,
        }[mcp_cfg.transport](transport=mcp_cfg.transport, url=mcp_cfg.url)
        for name, mcp_cfg in settings.cfg.mcp.items()
    }
    client = MultiServerMCPClient(connections)
    return await client.get_tools()
