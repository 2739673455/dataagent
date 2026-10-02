"""Assistant 的会话、消息、委派与召回契约。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from langchain_core.messages import BaseMessage
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    model_validator,
)

from app.metadata.contracts import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
)
from app.query.contracts import QueryExperienceRecallResult
from app.shared.contracts.analysis import IDENTIFIER_PATTERN, AgentType

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=IDENTIFIER_PATTERN.pattern,
    ),
]

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

MESSAGE_CREATED_AT_KEY = "dataagent_created_at"

DELEGATION_CONTEXT_KEY = "dataagent_delegation_context"


@dataclass(frozen=True, slots=True)
class PlannerTurnContext:
    """Turn 是处理一次用户输入的逻辑回合，可包含多次模型续写。

    中断后恢复仍处理同一回合；Run 则是承载执行与订阅的进程内实例。"""

    user_id: int
    conversation_id: UUID
    max_continuations: int

    def __post_init__(self) -> None:
        """校验 Planner 回合上下文中的身份和续写参数。"""
        if isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("user_id 必须为正整数")
        if self.max_continuations < 0:
            raise ValueError("max_continuations 不能为负数")


type SubagentRunStatus = Literal[
    "running",
    "completed",
    "failed",
    "cancelled",
]


@dataclass(frozen=True, slots=True)
class SubagentMessageActivity:
    """一次 Specialist 执行产生的公开候选消息。"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message: BaseMessage


@dataclass(frozen=True, slots=True)
class SubagentThinkingDeltaActivity:
    """一次 Specialist 模型调用产生的思考增量。"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message_id: str
    delta: str
    reset: bool = False


@dataclass(frozen=True, slots=True)
class SubagentMessageDeltaActivity:
    """一次 Specialist 模型调用产生的正文增量。"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message_id: str
    delta: str
    reset: bool = False


@dataclass(frozen=True, slots=True)
class SubagentStatusActivity:
    """一次 Specialist 执行的状态变化。"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    status: SubagentRunStatus


type SubagentActivity = (
    SubagentMessageActivity
    | SubagentThinkingDeltaActivity
    | SubagentMessageDeltaActivity
    | SubagentStatusActivity
)

type SubagentActivityWriter = Callable[[SubagentActivity], None]


@dataclass(frozen=True, slots=True)
class DelegationActivityHistory:
    """一次 delegation 的公开消息和真实执行状态。"""

    messages: list[BaseMessage]
    status: SubagentRunStatus


class StrictProtocolModel(BaseModel):
    """拒绝未知字段的协议模型基类。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class DelegationMessageContext(StrictProtocolModel):
    """持久化在 Specialist 输入消息中的委派边界。"""

    delegation_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    ]


class DelegationRequest(StrictProtocolModel):
    """Delegation 是 Planner 向专业 Agent 发起的一次工作委派。

    请求定位可复用的 Session；每次委派以独立 delegation_id 记录结果，
    同一 Session 可以接收多次委派并延续工作上下文。"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    message: NonEmptyText


class ListSessionsRequest(StrictProtocolModel):
    """查询当前 Conversation 内专业 Session 的请求。"""

    analysis_id: Identifier | None = None


class DeleteSessionRequest(StrictProtocolModel):
    """删除专业 Agent Session 的请求。"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier


class DelegationResult(StrictProtocolModel):
    """运行时记录的委派状态、Session 身份和 Agent 原始文本。"""

    status: Literal["completed", "failed"]
    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    content: str = Field(min_length=1)


class DelegationCheckpointRecord(StrictProtocolModel):
    """持久化一次委派的运行状态和文本结果。"""

    delegation_id: NonEmptyText
    status: SubagentRunStatus
    result: DelegationResult | None = None


class SessionSummary(StrictProtocolModel):
    """单个专业 Agent Session 的结构化摘要。"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    status: Literal[
        "active",
        "completed",
        "failed",
        "interrupted",
    ]
    summary: NonEmptyText | None = None
    updated_at: datetime | None = None


class ListSessionsResult(StrictProtocolModel):
    """当前 Conversation 内的专业 Session 列表。"""

    analysis_id: Identifier | None = None
    sessions: list[SessionSummary]


class DeleteSessionResult(StrictProtocolModel):
    """删除专业 Agent Session 的成功响应。"""

    status: Literal["success"] = "success"
    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    existed: bool
    message: NonEmptyText


class CreateConversationRequest(BaseModel):
    """创建对话请求。"""

    model_config = ConfigDict(extra="forbid")

    is_draft: bool = Field(default=False, description="是否创建草稿对话")
    initial_message: str | None = Field(
        default=None,
        description="用于初始化标题的首条用户文本",
    )


class DeleteConversationRequest(BaseModel):
    """删除对话请求。"""

    model_config = ConfigDict(extra="forbid")

    conversation_ids: list[UUID] = Field(
        ...,
        min_length=1,
        description="对话ID列表",
    )


class UpdateConversationRequest(BaseModel):
    """更新对话请求。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID = Field(..., description="对话ID")
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    ] = Field(description="对话标题")


class ConversationResponse(BaseModel):
    """对话响应。"""

    conversation_id: UUID
    title: str
    update_at: datetime
    running: bool


class ConversationListResponse(BaseModel):
    """对话列表响应。"""

    conversations: list[ConversationResponse]


class TextContent(BaseModel):
    """消息中的文本内容。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str = Field(..., description="文本内容")


class ImageContent(BaseModel):
    """消息中的图片内容。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["image_url"]
    image_url: str = Field(..., description="图片链接")


class ThinkingContent(BaseModel):
    """模型生成回答前的思考内容。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["thinking"]
    text: str = Field(..., description="思考内容")
    status: Literal["streaming", "complete", "interrupted"] = Field(
        default="complete",
        description="思考生成状态",
    )


class ToolCallPart(BaseModel):
    """消息中的工具调用内容。"""

    type: Literal["tool_call"]
    tool_call_id: str = Field(..., description="工具调用ID")
    name: str = Field(..., description="工具名称")
    args: dict = Field(default_factory=dict, description="工具参数")


class ToolResultPart(BaseModel):
    """消息中的工具结果内容。"""

    type: Literal["tool_result"]
    tool_call_id: str = Field(..., description="工具调用ID")
    name: str = Field(..., description="工具名称")
    content: str = Field(..., description="工具执行结果")


MessageRole = Literal["user", "assistant", "tool", "system"]

FinishReason = str

UserMessagePart = Annotated[
    TextContent | ImageContent,
    Field(discriminator="type"),
]

MessagePart = Annotated[
    TextContent | ImageContent | ThinkingContent | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]


class Attachment(BaseModel):
    """附件。"""

    f_path: str = Field(..., description="工作区内的文件路径")
    media_type: str | None = Field(default=None, description="附件媒体类型")
    description: str | None = Field(default=None, description="附件说明")


class AttachmentReference(BaseModel):
    """用户消息引用的已上传附件。"""

    model_config = ConfigDict(extra="forbid")

    f_path: str = Field(..., description="工作区内的文件路径")


class UserMessageRequest(BaseModel):
    """用户提交给 Agent 的消息。"""

    model_config = ConfigDict(extra="forbid")

    parts: list[UserMessagePart] = Field(..., description="文本和图片片段")
    attachments: list[AttachmentReference] | None = Field(
        default=None,
        description="已上传附件引用",
    )

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        """校验消息至少包含一个片段或附件。"""
        if not self.parts and not self.attachments:
            raise ValueError("消息内容或附件不能为空")
        return self


class MessageResponse(BaseModel):
    """返回给客户端的消息。"""

    message_id: str | None = Field(default=None, description="LangGraph 消息ID")
    created_at: datetime | None = Field(default=None, description="消息创建时间")
    role: MessageRole = Field(..., description="发送者")
    parts: list[MessagePart] = Field(..., description="消息片段")
    attachments: list[Attachment] | None = Field(default=None, description="附件列表")
    finish_reason: FinishReason | None = Field(default=None, description="完成原因")


class ChatStreamRequest(BaseModel):
    """SSE 聊天请求。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID = Field(..., description="对话ID")
    message: UserMessageRequest = Field(..., description="用户消息")


class DeleteAttachmentRequest(BaseModel):
    """删除附件请求。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID = Field(..., description="对话ID")
    f_path: str = Field(..., min_length=1, description="工作区内的文件路径")


class MessageListResponse(BaseModel):
    """消息列表响应。"""

    messages: list[MessageResponse]


class ConversationRunStatusResponse(BaseModel):
    """Conversation 后台 Planner Run 状态。"""

    running: bool


class SubagentMessageListResponse(BaseModel):
    """一次 Specialist delegation 的公开工作消息。"""

    status: Literal[
        "running",
        "completed",
        "failed",
        "cancelled",
    ]
    messages: list[MessageResponse]


class ChatStreamMessageEvent(BaseModel):
    """SSE 消息事件。"""

    type: Literal["message"]
    message: MessageResponse = Field(..., description="消息内容")


class ChatStreamThinkingEvent(BaseModel):
    """Planner 模型思考增量事件。"""

    type: Literal["thinking"]
    message_id: str = Field(..., description="所属 assistant 消息ID")
    delta: str = Field(..., description="本次新增的思考文本")
    reset: bool = Field(
        default=False,
        description="是否在追加本增量前清空该消息已有思考文本",
    )


class ChatStreamMessageDeltaEvent(BaseModel):
    """Planner assistant 正文增量事件。"""

    type: Literal["message_delta"]
    message_id: str = Field(..., description="所属 assistant 消息ID")
    delta: str = Field(..., description="本次新增的正文文本")
    reset: bool = Field(
        default=False,
        description="是否在追加本增量前清空该消息已有正文文本",
    )


class ChatStreamErrorEvent(BaseModel):
    """SSE 错误事件。"""

    type: Literal["error"]
    content: str = Field(..., description="错误信息")


class ChatStreamDoneEvent(BaseModel):
    """SSE 完成事件。"""

    type: Literal["done"]


class ChatStreamSubagentMessageEvent(BaseModel):
    """Specialist 执行期间产生的公开消息事件。"""

    type: Literal["subagent_message"]
    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message: MessageResponse


class ChatStreamSubagentThinkingEvent(BaseModel):
    """Specialist 模型思考增量事件。"""

    type: Literal["subagent_thinking"]
    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message_id: str = Field(..., description="所属 assistant 消息ID")
    delta: str = Field(..., description="本次新增的思考文本")
    reset: bool = Field(
        default=False,
        description="是否在追加本增量前清空该消息已有思考文本",
    )


class ChatStreamSubagentMessageDeltaEvent(BaseModel):
    """Specialist assistant 正文增量事件。"""

    type: Literal["subagent_message_delta"]
    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message_id: str = Field(..., description="所属 assistant 消息ID")
    delta: str = Field(..., description="本次新增的正文文本")
    reset: bool = Field(
        default=False,
        description="是否在追加本增量前清空该消息已有正文文本",
    )


class ChatStreamSubagentStatusEvent(BaseModel):
    """Specialist 执行状态事件。"""

    type: Literal["subagent_status"]
    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    status: Literal[
        "running",
        "completed",
        "failed",
        "cancelled",
    ]


ChatStreamEventPayload = Annotated[
    ChatStreamMessageEvent
    | ChatStreamThinkingEvent
    | ChatStreamMessageDeltaEvent
    | ChatStreamErrorEvent
    | ChatStreamDoneEvent
    | ChatStreamSubagentMessageEvent
    | ChatStreamSubagentThinkingEvent
    | ChatStreamSubagentMessageDeltaEvent
    | ChatStreamSubagentStatusEvent,
    Field(discriminator="type"),
]


class ChatStreamEvent(RootModel[ChatStreamEventPayload]):
    """单个 SSE data 帧的 JSON 事件。"""


class UploadAttachmentResponse(BaseModel):
    """上传附件响应。"""

    attachment: Attachment = Field(..., description="上传后的附件信息")


SemanticResourceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


class SemanticRecallRecord(BaseModel):
    """一次独立检索或多次检索的合并快照。"""

    model_config = ConfigDict(extra="forbid")

    user_id: int
    conversation_id: UUID
    query: str = Field(min_length=1, max_length=1000)
    request: SemanticResourceRecallRequest | None
    response: SemanticResourceRecallResponse
    query_experiences: list[QueryExperienceRecallResult]
    query_experiences_retrieved_at: datetime
    query_experience_role_name: str | None
    query_experience_authorization_fingerprint: str | None
    source_queries: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SemanticRecallUpdate:
    """一次召回事务的前后快照及本次实际检索结果。"""

    record: SemanticRecallRecord
    previous: SemanticRecallRecord | None
    recalled: SemanticResourceRecallResponse


class SemanticRecallColumnDeletion(BaseModel):
    """一个字段或其部分字段值的删除选择器。"""

    model_config = ConfigDict(extra="forbid")

    values: list[str] | None = Field(default=None, min_length=1)

    @property
    def deletes_entire_column(self) -> bool:
        """未指定字段值时删除整个字段。"""
        return self.values is None


class SemanticRecallTableDeletion(BaseModel):
    """一张表或其中部分字段的删除选择器。"""

    model_config = ConfigDict(extra="forbid")

    columns: dict[SemanticResourceName, SemanticRecallColumnDeletion] | None = Field(
        default=None, min_length=1
    )

    @property
    def deletes_entire_table(self) -> bool:
        """未指定字段时删除整张表。"""
        return self.columns is None


class SemanticRecallQueryExperienceDeletion(BaseModel):
    """一条查询经验的删除选择器。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID


class SemanticRecallMetricDeletion(BaseModel):
    """一个指标的删除选择器。"""

    model_config = ConfigDict(extra="forbid")


class SemanticRecallResourceDeletion(BaseModel):
    """一个 query 内待删除的语义上下文资源树。"""

    model_config = ConfigDict(extra="forbid")

    query: SemanticResourceName = Field(
        description=(
            "待删除资源所属的稳定 query 业务键，必须与 recall_context 使用的 query "
            "完全一致"
        )
    )
    tables: dict[SemanticResourceName, SemanticRecallTableDeletion] = Field(
        default_factory=dict
    )
    metrics: dict[SemanticResourceName, SemanticRecallMetricDeletion] = Field(
        default_factory=dict
    )
    query_experiences: list[SemanticRecallQueryExperienceDeletion] = Field(
        default_factory=list
    )

    @property
    def deletes_entire_query(self) -> bool:
        """未指定资源时删除整个 query 上下文。"""
        return not any(
            (
                self.tables,
                self.metrics,
                self.query_experiences,
            )
        )


class RecallContextRequest(SemanticResourceRecallRequest):
    """按稳定业务键补充检索并累计召回上下文。"""

    query: SemanticResourceName = Field(
        description="当前会话的稳定业务键；后续补充检索原样复用，只调整 terms 和 resource_types"
    )


class ListRecallsRequest(BaseModel):
    """读取最近召回记录。"""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=100, description="返回最近记录的数量")


class GetRecallRequest(BaseModel):
    """按稳定业务键读取召回记录。"""

    model_config = ConfigDict(extra="forbid")
    query: SemanticResourceName = Field(
        description="与 recall_context 完全一致的 query"
    )


class MergeRecallsRequest(BaseModel):
    """将来源上下文合并到目标。"""

    model_config = ConfigDict(extra="forbid")
    target_query: SemanticResourceName = Field(
        description="接收累计结果并保留的目标 query"
    )
    source_query: SemanticResourceName = Field(
        description="提供结果并在合并后删除的来源 query"
    )

    @model_validator(mode="after")
    def different_queries(self) -> MergeRecallsRequest:
        """校验合并来源与目标使用不同的召回业务键。"""
        if self.target_query == self.source_query:
            raise ValueError("目标 query 和来源 query 不能相同")
        return self


class DeleteRecallsRequest(BaseModel):
    """删除上下文或其中的资源；同一业务键仅能出现一次。"""

    model_config = ConfigDict(extra="forbid")
    deletions: list[SemanticRecallResourceDeletion] = Field(
        min_length=1, description="未提供资源选择器时删除整个 query"
    )

    @model_validator(mode="after")
    def unique_queries(self) -> DeleteRecallsRequest:
        """校验一次删除请求中每个召回业务键只出现一次。"""
        queries = [item.query for item in self.deletions]
        if len(set(queries)) != len(queries):
            raise ValueError("同一 query 只能出现一次")
        return self
