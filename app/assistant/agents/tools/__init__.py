"""Agent 共用工具导出；角色专用工具由调用方按模块加载。"""

from app.assistant.agents.tools.shell import create_shell_tool

__all__ = ["create_shell_tool"]
