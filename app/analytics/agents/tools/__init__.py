"""Specialist 通用工具"""

from app.analytics.agents.tools.shell import create_shell_tools
from app.analytics.agents.tools.view_image import create_image_view_request_tool

__all__ = ["create_image_view_request_tool", "create_shell_tools"]
