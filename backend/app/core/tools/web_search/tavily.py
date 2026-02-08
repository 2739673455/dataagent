from typing import Annotated, Literal

from app.config import CFG
from langchain.tools import tool
from tavily import TavilyClient

tavily_client = TavilyClient(CFG.tool.tavily.api_key)


# 网络搜索工具
@tool
def internet_search(
    query: Annotated[str, "需要进行搜索的查询内容"],
    max_results: Annotated[int, "返回的最大搜索结果数量，默认为5"] = 5,
    topic: Annotated[
        Literal["general", "news", "finance"],
        "搜索主题类别，可选值为 'general' (通用), 'news' (新闻), 'finance' (金融)，默认为 'general'",
    ] = "general",
    include_raw_content: Annotated[bool, "是否包含网页原始内容，默认为 False"] = False,
):
    """根据问题进行网络查询，当需要获取外部互联网的公开信息、最新新闻或特定主题数据时使用此工具"""
    if tavily_client is None:
        return "Error: 'tavily-python' library is not installed."

    # 使用 monitor 进行埋点
    monitor.report_tool("网络搜索工具", {"网络搜索的内容": query})
    try:
        results = tavily_client.search(
            query,
            max_results=max_results,
            include_raw_content=include_raw_content,
            topic=topic,
        )
        return results
    except Exception as e:
        raise e
