"""Specialist Shell Job 工具"""

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.assistant.agents.shell_jobs import ShellJobRuntime


def _dump(result: BaseModel) -> dict[str, Any]:
    """把公开结果模型转换为紧凑 JSON 字典"""
    return result.model_dump(mode="json", exclude_none=True)


def create_shell_tools(runtime: ShellJobRuntime) -> tuple[BaseTool, ...]:
    """创建绑定当前 Agent Run Registry 的四个 Shell 工具"""

    @tool("execute")
    async def execute(
        runtime_context: ToolRuntime,
        command: Annotated[
            str,
            Field(min_length=1, description="在当前 Session 工作目录执行的 Shell 命令"),
        ],
    ) -> dict[str, Any]:
        """启动 Shell 命令；前台等待 60 秒后仍未结束时返回后台 job_id。"""
        del runtime_context
        return _dump(await runtime.execute(command))

    @tool("list_shell_jobs")
    async def list_shell_jobs(
        runtime_context: ToolRuntime,
        include_reviewed: Annotated[
            bool,
            "是否同时列出已经查看过最终结果的终态任务",
        ] = False,
    ) -> list[dict[str, Any]]:
        """列出当前 Agent Run 的 Shell Job；读取列表不会标记结果为已查看。"""
        del runtime_context
        return [_dump(item) for item in runtime.list(include_reviewed=include_reviewed)]

    @tool("get_shell_job")
    async def get_shell_job(
        runtime_context: ToolRuntime,
        job_id: Annotated[str, Field(min_length=1, description="execute 返回的 job_id")],
        wait_seconds: Annotated[
            float,
            Field(ge=0, description="最多等待任务结束的秒数，0 表示立即返回"),
        ] = 0,
    ) -> dict[str, Any]:
        """查看或等待一个 Shell Job；返回终态时会把最终结果标记为已查看。"""
        del runtime_context
        return _dump(await runtime.get(job_id, wait_seconds=wait_seconds))

    @tool("cancel_shell_job")
    async def cancel_shell_job(
        runtime_context: ToolRuntime,
        job_id: Annotated[str, Field(min_length=1, description="execute 返回的 job_id")],
    ) -> dict[str, Any]:
        """取消一个 Shell Job，并终止命令所属的整个进程组。"""
        del runtime_context
        return _dump(await runtime.cancel(job_id))

    return execute, list_shell_jobs, get_shell_job, cancel_shell_job


__all__ = ["create_shell_tools"]
