"""业务提交使用中央 Celery 路由且保持失败语义。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from app.assistant.tasks import (
    enqueue_conversation_deletion,
    enqueue_conversation_title,
)
from app.metadata.task_submission import SYNC_COLUMN_INDEXES_TASK, submit_metadata_task
from app.query.task_scheduler import query_experience_index_scheduler
from app.shared.tasks.celery_app import celery_app
from app.workflows.tasks import enqueue_user_deletion

_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


@pytest.mark.parametrize(
    ("submit", "queue"),
    [
        (lambda: enqueue_conversation_title(1, _ID, "title", "text"), "lightweight"),
        (lambda: enqueue_conversation_deletion(1, _ID), "lifecycle"),
        (lambda: enqueue_user_deletion(1), "lifecycle"),
        (
            lambda: submit_metadata_task(SYNC_COLUMN_INDEXES_TASK, [["orders", "id"]]),
            "metadata-index",
        ),
        (lambda: query_experience_index_scheduler.enqueue(_ID, 1), "metadata-index"),
    ],
)
def test_submission_uses_configured_route(submit, queue: str) -> None:
    with patch.object(
        celery_app, "send_task", return_value=MagicMock(id="task")
    ) as send:
        submit()
    call = send.call_args
    assert "queue" not in call.kwargs
    assert "routing_key" not in call.kwargs
    route = celery_app.amqp.router.route({}, call.args[0])
    assert route["queue"].name == queue
    assert route["routing_key"] == queue


def test_query_submission_keeps_compensation_behavior() -> None:
    with patch.object(celery_app, "send_task", side_effect=RuntimeError("broker")):
        assert not query_experience_index_scheduler.enqueue(_ID, 1)
        with pytest.raises(RuntimeError, match="broker"):
            enqueue_user_deletion(1)
