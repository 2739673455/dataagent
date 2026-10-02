"""文本终答按委派边界读取，附件与普通 Agent 消息共用展示协议。"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.assistant.contracts import DELEGATION_CONTEXT_KEY
from app.assistant.services.specialist_checkpoint import SpecialistCheckpointView


def _boundary(delegation_id: str) -> HumanMessage:
    return HumanMessage(
        content="task",
        additional_kwargs={DELEGATION_CONTEXT_KEY: {"delegation_id": delegation_id}},
    )


def test_only_current_complete_answer_is_returned():
    view = SpecialistCheckpointView(
        {
            "messages": [
                _boundary("old"),
                AIMessage(content="old answer"),
                _boundary("current"),
                AIMessage(
                    content="current answer",
                    response_metadata={"finish_reason": "stopstop"},
                ),
                _boundary("next"),
                AIMessage(content="next answer"),
            ]
        }
    )
    assert view.final_response("current") == "current answer"
    assert view.final_response("missing") is None


def test_tool_work_does_not_reuse_previous_text_as_final_answer():
    view = SpecialistCheckpointView(
        {
            "messages": [
                _boundary("old"),
                AIMessage(content="old answer"),
                _boundary("current"),
                AIMessage(
                    content="查询中",
                    tool_calls=[{"id": "q", "name": "execute_sql", "args": {}}],
                ),
                ToolMessage(content="rows", tool_call_id="q"),
            ]
        }
    )
    assert view.final_response("current") is None
