from uuid import uuid4

import pytest

from app.sandbox.exceptions import SandboxDeletedError
from app.sandbox.ownership import LocalSandboxOwnership


def test_conversation_tombstone_blocks_target_and_allows_other_conversation() -> None:
    ownership = LocalSandboxOwnership()
    deleted_conversation = uuid4()
    available_conversation = uuid4()

    with ownership.conversation_maintenance(7, deleted_conversation):
        ownership.mark_conversation_deleted(7, deleted_conversation)

    with (
        pytest.raises(SandboxDeletedError),
        ownership.operation(7, deleted_conversation),
    ):
        pass
    with ownership.operation(7, available_conversation):
        pass


def test_user_tombstone_blocks_all_conversations() -> None:
    ownership = LocalSandboxOwnership()
    conversation_id = uuid4()

    with ownership.user_maintenance(7):
        ownership.mark_user_deleted(7)

    with pytest.raises(SandboxDeletedError):
        ownership.assert_available(7, conversation_id)
    with (
        pytest.raises(SandboxDeletedError),
        ownership.operation(7, conversation_id),
    ):
        pass
