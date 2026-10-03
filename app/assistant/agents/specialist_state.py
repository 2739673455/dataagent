"""各专家共用的委派持久化状态。"""

from typing import Annotated

from deepagents.graph import DeepAgentState


def _merge_delegation_records(
    current: dict[str, object],
    updates: dict[str, object],
) -> dict[str, object]:
    """按 delegation ID 覆盖单条状态，同时保留同 Session 的历史记录。"""
    return {**current, **updates}


class SpecialistAgentState(DeepAgentState):
    """增加显式 delegation 状态的专业 Agent Checkpoint。"""

    delegation_records: Annotated[
        dict[str, object],
        _merge_delegation_records,
    ]
