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

    message: Mapped[list['Message']] = relationship('Message', back_populates='conversation')


class Message(Base):
    __tablename__ = 'message'
    __table_args__ = (
        ForeignKeyConstraint(['conversation_id'], ['conversation.id'], ondelete='CASCADE', name='message_ibfk_1'),
        Index('idx_message_conversation_id', 'conversation_id'),
        {'comment': '消息'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='消息ID')
    conversation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='对话ID')
    role: Mapped[str] = mapped_column(String(10), nullable=False, comment='角色 (user/assistant/tool/system)')
    parts: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False, comment='消息片段列表 (JSON 字符串)')
    create_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间')
    yn: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'1'"), comment='是否启用')
    finish_reason: Mapped[Optional[str]] = mapped_column(String(128), comment='完成原因')
    attachments: Mapped[Optional[str]] = mapped_column(Text, comment='附件列表 (JSON 字符串)')

    conversation: Mapped['Conversation'] = relationship('Conversation', back_populates='message')
