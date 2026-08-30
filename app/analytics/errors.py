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


class ConversationNotResumableError(ProblemError):
    """表示会话当前没有可恢复的 Planner 待执行任务"""

    type = "conversation-not-resumable"
    title = "对话当前无法继续执行"
    status = HTTPStatus.CONFLICT


class ConversationRunAlreadyActiveError(ProblemError):
    """表示目标对话已有正在执行的 Planner Run"""

    type = "conversation-run-already-active"
    title = "对话正在执行"
    status = HTTPStatus.CONFLICT
