"""工具业务错误的公共响应字段。"""

from typing import Any


def tool_error(message: str, error: Exception, **fields: Any) -> dict[str, Any]:
    """保留业务错误字段，并统一异常类别及原因。"""
    return {
        "status": "error",
        "message": message,
        **fields,
        "details": [
            {
                "type": type(error).__name__,
                "msg": str(error).strip() or "异常未提供详情",
            }
        ],
    }
