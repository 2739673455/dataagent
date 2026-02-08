from typing import Annotated, Literal

from app.config import CFG
from langchain.tools import tool
from tavily import TavilyClient  # uv add tavily-python

tavily_client = TavilyClient(CFG.tool.tavily.api_key)


# 网络搜索工具
@tool
def internet_search(
    query: Annotated[str, "要搜索的内容"],
    max_results: Annotated[int, "返回的结果数量"] = 5,
    topic: Annotated[
        Literal["general", "news", "finance"],
        "搜索主题类别，可选值为 'general' (通用), 'news' (新闻), 'finance' (金融)，默认为 'general'",
    ] = "general",
):
    """从互联网搜索相关信息"""
    return tavily_client.search(
        query,
        max_results=max_results,
        topic=topic,
    )
