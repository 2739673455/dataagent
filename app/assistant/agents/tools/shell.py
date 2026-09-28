"""等待沙箱命令完成并返回输出。"""

import secrets
from typing import Annotated

from langchain.tools import tool
from langchain_core.tools import BaseTool
from loguru import logger
from pydantic import Field

from app.sandbox.shell_runner import DockerShellJobRunner


def create_shell_tools(executor: DockerShellJobRunner) -> tuple[BaseTool, ...]:
    """创建绑定当前工作目录的 Shell 工具。"""

    @tool("shell")
    async def shell(command: Annotated[str, Field(min_length=1)]) -> str:
        """在当前工作目录执行 Shell 命令，等待结束后返回输出；长输出附带文件路径。"""
        job_id = f"job_{secrets.token_hex(4)}"
        keep_log = False
        try:
            result = await executor.arun(job_id, command)
            output = result.output or result.error or ""
            if result.exit_code not in (None, 0):
                output += f"\nShell 命令以退出码 {result.exit_code} 结束"
            elif result.status != "completed" and not output:
                output = f"Shell 命令未完成: {result.status}"
            keep_log = result.output_inline_truncated
            if keep_log:
                output += (
                    f"\n详细输出文件: {executor.workspace_dir.rstrip('/')}"
                    f"/large_tool_results/shell_jobs/{job_id}.log"
                )
            return output
        finally:
            try:
                await executor.acleanup(job_id, remove_log=not keep_log)
            except Exception:  # noqa: BLE001
                logger.exception("清理 Shell 临时文件失败: job_id={}", job_id)

    return (shell,)
