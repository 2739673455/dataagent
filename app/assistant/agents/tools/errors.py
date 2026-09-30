"""工具业务错误的公共响应字段。"""

from typing import Any


def tool_error(message: str, error: Exception | str, **fields: Any) -> dict[str, Any]:
    """统一错误摘要和具体原因，并保留业务字段。"""
    return {
        "status": "error",
        "message": message,
        **fields,
        "error": str(error).strip() or "异常未提供详情",
    }
