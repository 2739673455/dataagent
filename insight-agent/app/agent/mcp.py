from app.core import settings
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import (
    SSEConnection,
    StdioConnection,
    StreamableHttpConnection,
    WebsocketConnection,
)

CONNECTION_MAP = {
    "sse": SSEConnection,
    "stdio": StdioConnection,
    "websocket": WebsocketConnection,
    "streamable_http": StreamableHttpConnection,
}


async def get_mcp_tools() -> list:
    """初始化 MCP 客户端并返回所有 MCP 工具"""
    connections = {
        name: CONNECTION_MAP[mcp_cfg.transport](
            transport=mcp_cfg.transport,
            url=mcp_cfg.url,
        )
        for name, mcp_cfg in settings.cfg.mcp.items()
    }
    client = MultiServerMCPClient(connections)
    return await client.get_tools()
