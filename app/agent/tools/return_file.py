from pathlib import PurePosixPath
from typing import Annotated, Any
from uuid import UUID

from langchain.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime

from app.clients.docker_sandbox_manager import (
    SandboxPathError,
    docker_sandbox_manager,
    normalize_attachment_path,
)


@tool
async def return_file(
    runtime: ToolRuntime,
    f_path: Annotated[str, "相对于当前工作区的文件路径"],
    f_name: Annotated[str | None, "返回给用户展示的文件名，可选"] = None,
) -> dict[str, Any]:
    """将当前工作区中的某个文件返回给用户"""
    configurable = runtime.config.get("configurable", {})
    user_id = configurable.get("user_id")
    conversation_id = configurable.get("conversation_id")
    if not isinstance(user_id, int) or not isinstance(conversation_id, str):
        return {"status": "error", "message": "sandbox context not found in config"}

    try:
        normalized_path = normalize_attachment_path(f_path.lstrip("/"))
    except SandboxPathError:
        return {"status": "error", "message": "path escapes workspace"}

    if not await docker_sandbox_manager.is_file(
        user_id,
        UUID(conversation_id),
        normalized_path,
    ):
        return {"status": "error", "message": "file not found"}

    return {
        # 操作状态标识，Agent 可据此判断文件返回是否成功
        "status": "success",
        # 人类可读的状态描述
        "message": "file returned",
        # 相对于工作区的文件路径，前端可拼接下载 URL
        "f_path": normalized_path,
        # 展示给用户的文件名，未提供时回退为路径中的文件名
        "f_name": f_name or PurePosixPath(normalized_path).name,
    }
