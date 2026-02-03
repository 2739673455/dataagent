import os
import datetime
import json
import logging
from typing import Any, Dict, List, Union
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.messages import BaseMessage

class AgentLogger:
    """
    Agent日志记录类，负责解耦日志功能。
    支持记录：
    1. 主智能体的Chunk (LangGraph State Update)
    2. 子智能体的Chunk (LLM Tokens via Callback)
    3. 工具调用的参数细节 (Tool Call via Callback)
    """
    def __init__(self, thread_id: str, project_root: str):
        self.thread_id = thread_id
        self.log_dir = os.path.join(project_root, "log")
        self.log_file = os.path.join(self.log_dir, f"agent_trace_{thread_id}.log")
        self._ensure_log_dir()
        
        # 写入日志头
        self._write_log("SYSTEM", f"Logger initialized for thread: {thread_id}")

    def _ensure_log_dir(self):
        try:
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)
        except Exception as e:
            print(f"[AgentLogger] Warning: Failed to create log directory: {e}")

    def _write_log(self, category: str, content: str):
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] [{category}]\n{content}\n{'-'*40}\n")
        except Exception as e:
            print(f"[AgentLogger] Error writing log: {e}")

    def log_main_chunk(self, chunk: Any):
        """记录主智能体的Chunk (Graph State)"""
        self._write_log("MAIN_AGENT_STATE_UPDATE", str(chunk))

    def log_tool_call(self, tool_name: str, args: Dict[str, Any]):
        """记录工具被调用时的参数细节"""
        try:
            args_str = json.dumps(args, ensure_ascii=False, indent=2)
        except:
            args_str = str(args)
        
        content = f"Tool Name: {tool_name}\nArguments:\n{args_str}"
        self._write_log("TOOL_CALL_DETAILS", content)

class AgentLogCallbackHandler(BaseCallbackHandler):
    """
    LangChain Callback Handler，用于捕获底层的 LLM 生成和工具调用。
    这可以捕获到子智能体的内部活动。
    """
    def __init__(self, logger: AgentLogger):
        self.logger = logger

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> Any:
        """LLM 开始生成时调用"""
        tags = kwargs.get("tags", [])
        self.logger._write_log("LLM_START", f"Tags: {tags}\nPrompts: {prompts[:1]}...") # 只记录第一个prompt的开头，避免太长

    def on_llm_new_token(self, token: str, **kwargs: Any) -> Any:
        """LLM 生成新 Token 时调用 (子智能体 Chunk)"""
        # 记录 Token，虽然碎，但满足用户需求
        # 为了避免日志文件过大，我们只在非空时记录，或者使用特殊格式
        if token:
             # 这里我们选择追加到文件，不换行，模拟流式效果不太可能，还是作为独立条目吧
             # 或者，我们只记录到内存 buffer，每积攒一定长度再 flush？
             # 为了简单和实时性，直接写。
             # 注意：这会产生海量日志行。
             # 另一种方案：只记录非空白字符
             if token.strip():
                 self.logger._write_log("LLM_TOKEN_CHUNK", token)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:
        """LLM 生成结束时调用"""
        generations = response.generations
        for gen_list in generations:
            for gen in gen_list:
                self.logger._write_log("LLM_OUTPUT", gen.text)

    def on_tool_start(
        self, serialized: Dict[str, Any], input_str: str, **kwargs: Any
    ) -> Any:
        """工具开始执行时调用"""
        name = serialized.get("name", "unknown")
        self.logger._write_log("TOOL_START", f"Tool: {name}\nInput: {input_str}")

    def on_tool_end(self, output: str, **kwargs: Any) -> Any:
        """工具执行结束时调用"""
        self.logger._write_log("TOOL_END", f"Output: {output}")

    def on_chain_start(
        self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any
    ) -> Any:
        """Chain (Agent) 开始执行时调用"""
        name = serialized.get("name", "unknown") if serialized else "unknown"
        tags = kwargs.get("tags", [])
        # 过滤掉一些杂讯，只记录重要的 Chain
        if tags and "seq:step" not in tags:
             self.logger._write_log("CHAIN_START", f"Chain: {name}\nTags: {tags}\nInputs: {str(inputs)[:500]}...")

