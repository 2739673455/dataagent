from pathlib import Path
from typing import Annotated, Any

from langchain.tools import tool


def create_return_file_tool(workspace_dir: Path):
    """创建绑定到指定会话工作区的返回文件工具"""

    @tool
    def return_file(
        path: Annotated[str, "相对于当前工作区的文件路径"],
        raw_name: Annotated[str | None, "返回给用户展示的原始文件名，可选"] = None,
    ) -> dict[str, Any]:
        """将当前工作区中的某个文件返回给用户"""
        if not path:
            return {
                "status": "error",
                "message": "path is required",
            }

        # 兼容模型把工作区根目录文件写成 /foo.txt 的情况，统一归一化为相对路径
        normalized_path = path.lstrip("/")
        candidate = (workspace_dir / normalized_path).resolve()
        if workspace_dir.resolve() not in candidate.parents:
            return {
                "status": "error",
                "message": "path escapes workspace",
            }

        if not candidate.is_file():
            return {
                "status": "error",
                "message": "file not found",
            }

        return {
            "status": "success",
            "path": normalized_path,
            "raw_name": raw_name or Path(normalized_path).name,
        }

    return return_file
