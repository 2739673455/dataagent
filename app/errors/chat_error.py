from http import HTTPStatus

from app.errors.base import ProblemError


class ConversationNotFoundError(ProblemError):
    type = "conversation-not-found"
    title = "对话不存在"
    status = HTTPStatus.NOT_FOUND


class SemanticRecallNotFoundError(ProblemError):
    type = "semantic-recall-not-found"
    title = "语义召回记录不存在"
    status = HTTPStatus.NOT_FOUND
