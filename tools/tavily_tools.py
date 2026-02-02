try:
    from typing import Annotated, Literal
except ImportError:
    from typing_extensions import Annotated, Literal
from langchain_core.tools import tool
from tavily import TavilyClient


# --- 引入 Monitor ---
import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from api.monitor import monitor
# --------------------

# Initialize Tavily Client
if TavilyClient:
    tavily_client = TavilyClient(api_key="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")
else:
    tavily_client = None

# 网络搜索工具
@tool
def internet_search(
    query: Annotated[str, "需要进行搜索的查询内容"],
    max_results: Annotated[int, "返回的最大搜索结果数量，默认为5"] = 5,
    topic: Annotated[Literal["general", "news", "finance"], "搜索主题类别，可选值为 'general' (通用), 'news' (新闻), 'finance' (金融)，默认为 'general'"] = "general",
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
