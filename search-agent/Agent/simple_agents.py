import asyncio
import uuid
import yaml
import os
import sys

# 将项目根目录添加到 sys.path，确保能导入 tools 和 api
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

import datetime
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent
from tools import get_assistant_list, create_ask_delete
from tools import convert_md_to_pdf
from tools import generate_markdown
from tools import internet_search
from tools import read_file_content
from tools import list_sql_tables, get_table_data, execute_sql_query
from api.monitor import monitor
from api.context import set_session_context, reset_session_context, set_thread_context

from langchain_core.messages import ToolMessage, AIMessage

# 加载 YAML 配置
def load_prompts(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

# YAML 文件位于 ../prompt/prompts.yaml (相对于当前脚本)
# 获取当前脚本的绝对路径
current_dir = os.path.dirname(os.path.abspath(__file__))
# 构建 prompts.yaml 的绝对路径
prompt_path = os.path.join(current_dir, '..', 'prompt', 'prompts.yaml')

if not os.path.exists(prompt_path):
    raise FileNotFoundError(f"Prompts file not found at: {prompt_path}")

prompts_config = load_prompts(prompt_path)
main_agent_config = prompts_config.get('main_agent', {})
sub_agents_config = prompts_config.get('sub_agents', {})

# --- 配置 ---
load_dotenv()

model = ChatOpenAI(
    model="qwen-max",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("DASHSCOPE_BASE_URL")
)

# --- 定义子 Agent ---
# 搜索助手
tavily_assistant = {
    "name": sub_agents_config['tavily']['name'],
    "description": sub_agents_config['tavily']['description'],
    "system_prompt": sub_agents_config['tavily']['system_prompt'],
    "tools": [internet_search],
}
# 数据库查询助手
subagent_db = {
    "name": sub_agents_config['db']['name'],
    "description": sub_agents_config['db']['description'],
    "system_prompt": sub_agents_config['db']['system_prompt'],
    "tools": [list_sql_tables, get_table_data, execute_sql_query],
}
# RAGFlow助手
subagent_ragflow = {
    "name": sub_agents_config['ragflow']['name'],
    "description": sub_agents_config['ragflow']['description'],
    "system_prompt": sub_agents_config['ragflow']['system_prompt'],
    "tools": [get_assistant_list, create_ask_delete],
}

subagents_list = [
    tavily_assistant,
    subagent_db,
    subagent_ragflow,
]

# --- 创建主 Agent ---

# print("--- 正在创建包含子Agent的主Agent ---")

main_agent = create_deep_agent(
    model=model,
    subagents=subagents_list,
    middleware=[],
    tools=[generate_markdown, convert_md_to_pdf, read_file_content],
    system_prompt=main_agent_config['system_prompt']
)


async def run_deep_agent(task_query: str, thread_id: str = None):
    """
    封装好的执行函数，供 API 调用
    """
    if not thread_id:
        thread_id = str(uuid.uuid4())
    
    print(f"--- 开始执行任务: {task_query} (Thread: {thread_id}) ---")
    
    # --- 任务工作目录 ---
    # 使用 project_root 确保路径正确
    output_root = os.path.join(project_root, "output")
    updated_root = os.path.join(project_root, "updated")
    
    # 直接使用 thread_id 作为目录名
    # 前端负责生成唯一的 thread_id (UUID)，后端直接使用，保证一一对应
    session_dir = os.path.join(output_root, f"session_{thread_id}")
    updated_session_dir = os.path.join(updated_root, f"session_{thread_id}")
    
    # 构建虚拟路径以满足Monitor/Framework的要求 (去除盘符，转为正斜杠)
    drive, path_without_drive = os.path.splitdrive(session_dir)
    virtual_session_dir = path_without_drive.replace("\\", "/")
    
    # 计算相对于项目根目录的相对路径，供 Agent 使用
    # 这样内置工具(如 read_file)在 CWD 下也能找到文件
    relative_session_dir = os.path.relpath(session_dir, project_root).replace("\\", "/")
    
    # 检查是否有上传的文件
    uploaded_files_info = ""
    if os.path.exists(updated_session_dir):
        relative_updated_dir = os.path.relpath(updated_session_dir, project_root).replace("\\", "/")
        
        # 获取目录下所有文件名
        try:
            files_in_dir = os.listdir(updated_session_dir)
            files_list_str = "\n".join([f"    - {f}" for f in files_in_dir])
        except Exception:
            files_list_str = "    (无法列出文件)"

        uploaded_files_info = f"\n    用户上传的参考文件位于：{relative_updated_dir}\n    包含以下文件：\n{files_list_str}\n    请使用上传文件分析工具参考此目录下的文件内容。"

    print(f"[System] 目标工作目录: {session_dir}")
    print(f"[System] 相对工作目录: {relative_session_dir}")

    # 设置当前线程的 Thread ID 上下文 (Monitor 需要)
    thread_token = set_thread_context(thread_id)

    try:
        if not os.path.exists(session_dir):
            print(f"[System] 目录不存在，正在创建...")
            os.makedirs(session_dir, exist_ok=True)
        else:
            print(f"[System] 目录已存在，直接复用")
    except Exception as e:
        print(f"\n[System] 创建/访问工作目录失败: {e}")
        # 降级处理，使用临时目录或其他逻辑
        session_dir = output_root
        drive, path_without_drive = os.path.splitdrive(session_dir)
        virtual_session_dir = path_without_drive.replace("\\", "/")
        relative_session_dir = os.path.relpath(session_dir, project_root).replace("\\", "/")

    # 无论成功与否，都报告最终的 Session 目录
    monitor.report_session_dir(virtual_session_dir)
    
    # 设置当前线程的会话目录上下文 (使用最终路径)
    token = set_session_context(session_dir)

    # 将路径约束注入到任务描述中
    path_instruction = f"""

    【系统强制指令】
    当前工作目录（Workspace）为：
    {relative_session_dir}

    {uploaded_files_info}

    规则：
    1. 如果用户上传了文件，则首先通过工具去解读上传的文件，然后综合用户提问进行后续处理
    2. 所有新生成的文件必须保存到工作目录下（请使用包含目录的完整相对路径，例如 '{relative_session_dir}/filename.md'）。
    3. 如果用户要求"新建文件"，请直接在工作目录下创建。
    4. 请使用相对路径，不要使用以 / 或盘符开头的绝对路径。
    5. 不要重复创建目录，例如不要在 '{relative_session_dir}' 下再创建名为 '{os.path.basename(session_dir)}' 的目录。
    """

    full_task = task_query + path_instruction

    config = {
        "configurable": {"thread_id": thread_id},
        "tags": ["deepagents"],
        "metadata": {"thread_id": thread_id},
    }

    # 使用 stream 打印过程
    # --- 日志系统初始化 ---
    from api.logger import AgentLogger, AgentLogCallbackHandler
    logger = AgentLogger(thread_id, project_root)
    callback_handler = AgentLogCallbackHandler(logger)
    
    # 将 Callback 注入到 Config 中，以便捕获底层 LLM 和 Tool 事件
    config["callbacks"] = [callback_handler]

    try:
        async for chunk in main_agent.astream({"messages": [{"role": "user", "content": full_task}]}, config=config):
            # --- 记录日志 ---
            logger.log_main_chunk(chunk)

            print(chunk)
            for node, state in chunk.items():
                if state is None:
                    continue
                if "messages" in state:
                    messages = state["messages"]
                    if isinstance(messages, list) and messages:
                        last_msg = messages[-1]
                        
                        # 尝试获取并打印工具调用的详细信息
                        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
                             for tool_call in last_msg.tool_calls:
                                # 记录工具调用参数
                                logger.log_tool_call(tool_call['name'], tool_call['args'])
                                
                                if tool_call['name'] == 'task':
                                    # 特殊处理 'task' 工具，这是主 Agent 委托任务给子 Agent 的行为
                                    subagent = tool_call['args'].get('subagent_type', '未知助手')
                                    desc = tool_call['args'].get('description', '无描述')
                                    
                                    # 打印主智能体传给子智能体的具体提示词
                                    print(f"\n{'='*20}\n[Main Agent -> Sub Agent ({subagent})]\nPrompt/Instruction:\n{desc}\n{'='*20}\n")
                                    
                                    monitor.report_assistant(subagent, {"任务描述": desc})
            
            # 检查是否有最终回复
            for node, state in chunk.items():
                if state and "messages" in state:
                    msgs = state["messages"]
                    if msgs and isinstance(msgs, list):
                        last_msg = msgs[-1]
                        if isinstance(last_msg, AIMessage) and last_msg.content:
                             # 只有当内容不为空，且不是工具调用时，才认为是最终回复
                             if not last_msg.tool_calls:
                                 final_response = last_msg.content
                                 monitor.report_task_result(final_response)

        return "Done"
    except Exception as e:
        print(f"Error executing agent: {e}")
        monitor._emit("error", f"Agent execution failed: {str(e)}")
        return f"Error: {str(e)}"
    finally:
        # 清理上下文
        if 'token' in locals():
            reset_session_context(token, thread_token)

if __name__ == "__main__":
    # --- 运行演示 ---

    # task = "查询一下公司内部的DeepAgents知识，以及网络上关于DeepAgents的知识，以及可以查询数据库中DeepAgents的数据信息，并使用保存工具存为word文档。"
    task = "创建一个空目录"
    
    asyncio.run(run_deep_agent(task))
