from app.config import CFG
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

connections = {
    name: CONNECTION_MAP[mcp_cfg.transport](
        transport=mcp_cfg.transport, url=mcp_cfg.url
    )
    for name, mcp_cfg in CFG.mcp.items()
}
mcp_client = MultiServerMCPClient(connections)
