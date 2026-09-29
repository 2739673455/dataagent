"""等待沙箱命令完成并返回输出。"""

from typing import Annotated

from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import Field

from app.sandbox import DockerSandboxBackend


def create_shell_tool(backend: DockerSandboxBackend) -> BaseTool:
    """创建绑定当前工作目录的 Shell 工具。"""

    @tool("shell")
    async def shell(command: Annotated[str, Field(min_length=1)]) -> str:
        """在当前工作目录执行 Shell 命令，等待结束后返回输出；长输出附带文件路径。"""
        result = await backend.run_shell(command)
        output = result.output or result.error or ""
        if result.exit_code not in (None, 0):
            output += f"\nShell 命令以退出码 {result.exit_code} 结束"
        elif result.status != "completed" and not output:
            output = f"Shell 命令未完成: {result.status}"
        if result.output_path:
            output += f"\n详细输出文件: {result.output_path}"
        return output

    return shell
