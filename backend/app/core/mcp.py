import asyncio

from app.config import CFG
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StreamableHttpConnection

client = MultiServerMCPClient(
    {
        "tavily": StreamableHttpConnection(
            transport="streamable_http", url=CFG.tool.tavily.mcp_url
        )
    }
)


async def main():
    tools = await client.get_tools()
    print(tools)


asyncio.run(main())
