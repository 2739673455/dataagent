"""Agent Shell Job 工具。"""

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.assistant.agents.shell_jobs import (
    SHELL_JOB_MAX_STATUS_WAIT_SECONDS,
    ShellJobRuntime,
)


def _dump(result: BaseModel) -> dict[str, Any]:
    """把公开结果模型转换为紧凑 JSON 字典。"""
    return result.model_dump(mode="json", exclude_none=True)


def create_shell_tools(runtime: ShellJobRuntime) -> tuple[BaseTool, ...]:
    """创建绑定当前 Agent Run Registry 的四个 Shell 工具。"""

    @tool("shell")
    async def shell(
        runtime_context: ToolRuntime,
        command: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "在当前 Session 工作目录执行的 Shell 命令；相对路径从该目录"
                    "解析，绝对路径直接使用。"
                ),
            ),
        ],
    ) -> str | dict[str, Any]:
        """运行 Shell 命令；前台截断输出附带路径，超时后返回后台 job_id。"""
        del runtime_context
        result = await runtime.start(command)
        return result if isinstance(result, str) else _dump(result)

    @tool("list_shell_jobs")
    async def list_shell_jobs(
        runtime_context: ToolRuntime,
    ) -> list[dict[str, Any]]:
        """列出当前 Agent Run 尚未消费的后台 Shell Job。"""
        del runtime_context
        return [_dump(item) for item in runtime.list()]

    @tool("get_shell_job")
    async def get_shell_job(
        runtime_context: ToolRuntime,
        job_id: Annotated[str, Field(min_length=1, description="shell 返回的 job_id")],
        wait_seconds: Annotated[
            float,
            Field(
                ge=0,
                le=SHELL_JOB_MAX_STATUS_WAIT_SECONDS,
                description="最多等待任务结束的秒数，范围 0 到 60，0 表示立即返回",
            ),
        ] = 0,
    ) -> dict[str, Any]:
        """查看或短暂等待 Shell Job；完整输出在 output_path，终态仅可读取一次。"""
        del runtime_context
        return _dump(await runtime.get(job_id, wait_seconds=wait_seconds))

    @tool("cancel_shell_job")
    async def cancel_shell_job(
        runtime_context: ToolRuntime,
        job_id: Annotated[str, Field(min_length=1, description="shell 返回的 job_id")],
    ) -> dict[str, Any]:
        """取消一个 Shell Job，并终止命令所属的整个进程组；终态会被消费。"""
        del runtime_context
        return _dump(await runtime.cancel(job_id))

    return shell, list_shell_jobs, get_shell_job, cancel_shell_job
