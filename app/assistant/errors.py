"""助手会话业务错误。"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class ConversationNotFoundError(ProblemError):
    """表示目标助手会话不存在。"""

    type = "conversation-not-found"
    title = "对话不存在"
    status = HTTPStatus.NOT_FOUND


class SubagentRunNotFoundError(ProblemError):
    """表示目标 Specialist delegation 不存在。"""

    type = "subagent-run-not-found"
    title = "子 Agent 执行记录不存在"
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
    """表示目标对话正在运行或执行生命周期操作。"""

    type = "conversation-busy"
    title = "对话正在处理中"
    status = HTTPStatus.CONFLICT


class PathTraversalError(ProblemError):
    """表示请求路径越过允许的沙箱边界。"""

    type = "path-traversal"
    title = "路径穿越"
    status = HTTPStatus.FORBIDDEN


class AttachmentNotFoundError(ProblemError):
    """表示目标沙箱附件不存在。"""

    type = "attachment-not-found"
    title = "附件不存在"
    status = HTTPStatus.NOT_FOUND


class AttachmentTooLargeError(ProblemError):
    """表示附件大小超过上传限制。"""

    type = "attachment-too-large"
    title = "附件过大"
    status = HTTPStatus.CONTENT_TOO_LARGE
