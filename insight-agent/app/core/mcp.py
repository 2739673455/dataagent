"""加载 MCP 工具"""

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

# 按配置中的 transport 字段构造每个 MCP 服务的连接实例
connections = {
    name: CONNECTION_MAP[mcp_cfg.transport](
        transport=mcp_cfg.transport, url=mcp_cfg.url
    )
    for name, mcp_cfg in CFG.mcp.items()
}

# 将多个 MCP 服务连接聚合成一个统一客户端
mcp_client = MultiServerMCPClient(connections)
