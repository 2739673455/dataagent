import os
import logging
import time
from api.monitor import monitor
import requests
from ragflow_sdk import RAGFlow
from dotenv import load_dotenv
from langchain_core.tools import tool
try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated


logger = logging.getLogger(__name__)


from typing import Union, Tuple, Optional

def _load_ragflow_env() -> Tuple[Optional[str], Optional[str]]:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(current_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        load_dotenv()

    api_key = os.getenv("RAGFLOW_API_KEY")
    base_url = os.getenv("RAGFLOW_API_URL")
    return api_key, base_url


@tool
def get_assistant_list(
    dummy_arg: Annotated[str, "不需要输入参数，直接调用即可"] = "",
):
    """
    获取RAGFlow中的所有聊天助手信息，并返回组合后的字符串。
    如果需要知道有哪些助手可用，请使用此工具。
    """
    monitor.report_tool("RAGFlow助手列表查询")
    api_key, base_url = _load_ragflow_env()

    if not api_key or not base_url:
        return "RAGFlow 环境变量未配置：请设置 RAGFLOW_API_URL 与 RAGFLOW_API_KEY。"

    if RAGFlow is None:
        return "Error: 'ragflow_sdk' or 'ragflow' library is not installed."

    result = ""
    try:
        rag_object = RAGFlow(api_key=api_key, base_url=base_url)

        for assistant in rag_object.list_chats():
            kb_names = []
            if assistant.datasets and isinstance(assistant.datasets, list):
                for dataset in assistant.datasets:
                    if isinstance(dataset, dict) and "name" in dataset:
                        kb_names.append(dataset["name"])

            kb_names_str = "、".join(kb_names) if kb_names else "无"
            result += (
                f"助手名称：{assistant.name}； 功能介绍：{assistant.description}； 知识库：{kb_names_str}\n"
            )

        if result:
            result = result.rstrip("\n")
    except Exception as e:
        result = f"获取助手列表时出错: {str(e)}"

    return result


@tool
def create_ask_delete(
    assistant_name: Annotated[str, "助手的名称"],
    question: Annotated[str, "要问的问题"],
) -> str:
    """
    创建一个新会话，提问一次，然后删除该会话，并返回答案。
    当需要向特定的RAGFlow助手提问时，使用此工具。
    """
    monitor.report_tool("RAGFlow助手提问工具", {"助手的名称": assistant_name,"查询的问题": question})
    api_key, base_url = _load_ragflow_env()
    try:
        test_response = requests.get(f"{base_url}/health", timeout=5)
        if test_response.status_code != 200:
            return f"RAGFlow 服务健康检查失败: status_code={test_response.status_code}"
    except requests.RequestException as e:
        return f"连接RAGFlow服务失败: {str(e)}"

    try:
        rag_object = RAGFlow(api_key=api_key, base_url=base_url)

        assistants = rag_object.list_chats(name=assistant_name)
        if not assistants:
            return f"未找到名为 '{assistant_name}' 的助手。"

        assistant = assistants[0]

        session = None
        try:
            session = assistant.create_session(name="temp_session_for_single_ask")
            response_generator = session.ask(question, stream=True)
            
            # 收集流式响应
            full_answer = ""
            for part in response_generator:
                if hasattr(part, 'content') and part.content:
                    full_answer = part.content

            monitor.report_tool("RAGFlow助手获取的答案", {"助手的名称": assistant_name, "查询的答案": full_answer})
            if session and hasattr(session, 'id'):
                assistant.delete_sessions(ids=[session.id])
            return full_answer

        except Exception as e:
            return f"提问过程中出错: {str(e)}"
        finally:
            if session:
                try:
                    # session.delete() # 假设的方法
                    pass 
                except:
                    pass

    except Exception as e:
        return f"RAGFlow 操作失败: {str(e)}"

