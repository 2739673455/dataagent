"""助手会话业务错误。"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class ConversationNotFoundError(ProblemError):
    """表示目标助手会话不存在。"""

    type = "conversation-not-found"
    title = "对话不存在"
    status = HTTPStatus.NOT_FOUND


class ConversationNotResumableError(ProblemError):
    """表示会话当前没有可恢复的 Planner 待执行任务。"""

    type = "conversation-not-resumable"
    title = "对话当前无法继续执行"
    status = HTTPStatus.CONFLICT


class ConversationRunConflictError(ProblemError):
    """表示目标对话已有正在执行的 Planner Run。"""

    type = "conversation-run-already-active"
    title = "对话正在执行"
    status = HTTPStatus.CONFLICT


class ConversationBusyError(ProblemError):
    """表示回合在受理完成前已停止。"""

    type = "conversation-busy"
    title = "对话正在处理中"
    status = HTTPStatus.CONFLICT


class AttachmentNotFoundError(ProblemError):
    """表示目标沙箱附件不存在。"""

    type = "attachment-not-found"
    title = "附件不存在"
    status = HTTPStatus.NOT_FOUND
