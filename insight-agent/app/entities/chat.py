from typing import Optional
import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, Index, String, Text, text
from sqlalchemy.dialects.mysql import MEDIUMTEXT, TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = 'conversation'
    __table_args__ = (
        Index('idx_conversation_user_id', 'user_id'),
        {'comment': '对话'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='对话ID')
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='用户ID')
    title: Mapped[str] = mapped_column(String(128), nullable=False, comment='对话标题')
    is_draft: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'0'"), comment='是否为草稿对话')
    create_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间')
    update_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), comment='更新时间')
    yn: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'1'"), comment='是否启用')

    context_compaction: Mapped[list['ContextCompaction']] = relationship('ContextCompaction', back_populates='conversation')
    message: Mapped[list['Message']] = relationship('Message', back_populates='conversation')


class ContextCompaction(Base):
    __tablename__ = 'context_compaction'
    __table_args__ = (
        ForeignKeyConstraint(['conversation_id'], ['conversation.id'], ondelete='CASCADE', name='context_compaction_ibfk_1'),
        Index('idx_context_compaction_conversation_id', 'conversation_id'),
        Index('idx_context_compaction_end_seq', 'conversation_id', 'end_seq'),
        {'comment': '上下文压缩事件'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='上下文压缩ID')
    conversation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='对话ID')
    end_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='本次压缩覆盖的结束上下文顺序号(0-based, 不包含)')
    summary_message: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False, comment='压缩后的摘要内容')
    create_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间')
    yn: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'1'"), comment='是否启用')

    conversation: Mapped['Conversation'] = relationship('Conversation', back_populates='context_compaction')


class Message(Base):
    __tablename__ = 'message'
    __table_args__ = (
        ForeignKeyConstraint(['conversation_id'], ['conversation.id'], ondelete='CASCADE', name='message_ibfk_1'),
        Index('idx_message_conversation_id', 'conversation_id'),
        Index('uk_message_conversation_id_context_seq', 'conversation_id', 'context_seq', unique=True),
        {'comment': '消息'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='消息ID')
    conversation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='对话ID')
    context_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='对话内上下文顺序号(从0起)')
    role: Mapped[str] = mapped_column(String(10), nullable=False, comment='角色 (user/assistant/tool/system)')
    parts: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False, comment='消息片段列表 (JSON 字符串)')
    create_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间')
    yn: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'1'"), comment='是否启用')
    finish_reason: Mapped[Optional[str]] = mapped_column(String(128), comment='完成原因')
    attachments: Mapped[Optional[str]] = mapped_column(Text, comment='附件列表 (JSON 字符串)')

    conversation: Mapped['Conversation'] = relationship('Conversation', back_populates='message')
