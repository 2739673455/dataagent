from pathlib import Path
from typing import Annotated, Any

from langchain.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime


@tool
def return_file(
    runtime: ToolRuntime,
    f_path: Annotated[str, "相对于当前工作区的文件路径"],
    f_name: Annotated[str | None, "返回给用户展示的文件名，可选"] = None,
) -> dict[str, Any]:
    """将当前工作区中的某个文件返回给用户"""
    # 获取工作区目录
    workspace_dir = runtime.config.get("configurable", {}).get("workspace_dir")
    if workspace_dir is None:
        return {"status": "error", "message": "workspace_dir not found in config"}
    workspace_dir = Path(workspace_dir).resolve()

    # 兼容模型把工作区根目录文件写成 /foo.txt 的情况，统一归一化为相对路径
    normalized_path = f_path.lstrip("/")
    # 拼接绝对路径
    candidate = (workspace_dir / normalized_path).resolve()
    if workspace_dir not in candidate.parents:
        # 阻止路径逃逸
        return {"status": "error", "message": "path escapes workspace"}
    if not candidate.is_file():
        # 文件不存在
        return {"status": "error", "message": "file not found"}

    return {
        # 操作状态标识，Agent 可据此判断文件返回是否成功
        "status": "success",
        # 人类可读的状态描述
        "message": "file returned",
        # 相对于工作区的文件路径，前端可拼接下载 URL
        "f_path": normalized_path,
        # 展示给用户的文件名，未提供时回退为路径中的文件名
        "f_name": f_name or Path(normalized_path).name,
    }
