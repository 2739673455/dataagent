"""跨模块召回清理操作的事务边界。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.metadata.services.recall_cleanup import RecallCleanupService


@pytest.mark.parametrize("scope", ["conversation", "user"])
@pytest.mark.parametrize("fails", [False, True])
def test_cleanup_commits_or_rolls_back_before_releasing_session(scope, fails) -> None:
    postgres = MagicMock()
    session = MagicMock()
    postgres.session.return_value.__aenter__.return_value = session
    transaction = session.begin.return_value
    repo = MagicMock(delete_all=AsyncMock(), delete_all_by_user=AsyncMock())
    operation = repo.delete_all if scope == "conversation" else repo.delete_all_by_user
    failure = RuntimeError("delete failed") if fails else None
    operation.side_effect = failure
    conversation_id = uuid4()
    service = RecallCleanupService(postgres)

    async def run():
        if scope == "conversation":
            await service.delete_conversation(7, conversation_id)
        else:
            await service.delete_user(7)

    with patch(
        "app.metadata.services.recall_cleanup.SemanticRecallPGRepo", return_value=repo
    ):
        if fails:
            with pytest.raises(RuntimeError, match="delete failed"):
                asyncio.run(run())
        else:
            asyncio.run(run())
    if scope == "conversation":
        operation.assert_awaited_once_with(7, conversation_id)
    else:
        operation.assert_awaited_once_with(7)
    transaction.__aenter__.assert_awaited_once()
    transaction.__aexit__.assert_awaited_once()
    postgres.session.return_value.__aexit__.assert_awaited_once()
    exception_type, exception, _ = transaction.__aexit__.call_args.args
    assert exception_type is (RuntimeError if fails else None)
    assert exception is failure
