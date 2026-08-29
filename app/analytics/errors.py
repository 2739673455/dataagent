"""分析会话业务错误"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class ConversationNotFoundError(ProblemError):
    """表示目标分析会话不存在"""

    type = "conversation-not-found"
    title = "对话不存在"
    status = HTTPStatus.NOT_FOUND


class SubagentRunNotFoundError(ProblemError):
    """表示目标 Specialist delegation 不存在"""

    type = "subagent-run-not-found"
    title = "子 Agent 执行记录不存在"
    status = HTTPStatus.NOT_FOUND
