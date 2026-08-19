from http import HTTPStatus

from app.errors.base import ProblemError


class ConversationNotFoundError(ProblemError):
    type = "conversation-not-found"
    title = "对话不存在"
    status = HTTPStatus.NOT_FOUND
