from typing import Optional
import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, Index, JSON, String, Text, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class ModelConfig(Base):
    __tablename__ = 'model_config'
    __table_args__ = (
        Index('idx_model_config_user_id', 'user_id'),
        {'comment': '用户模型配置'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='模型配置ID')
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='用户ID')
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment='配置名称')
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, comment='URL')
    create_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间')
    update_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), comment='更新时间')
    model_name: Mapped[Optional[str]] = mapped_column(String(100), comment='模型名称')
    api_key: Mapped[Optional[str]] = mapped_column(Text, comment='加密后的 API-Key')
    params: Mapped[Optional[dict]] = mapped_column(JSON, comment='模型调用参数')

    conversation: Mapped[list['Conversation']] = relationship('Conversation', back_populates='model_config')


class Conversation(Base):
    __tablename__ = 'conversation'
    __table_args__ = (
        ForeignKeyConstraint(['model_config_id'], ['model_config.id'], name='conversation_ibfk_1'),
        Index('idx_conversation_user_id', 'user_id'),
        Index('model_config_id', 'model_config_id'),
        {'comment': '对话'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='对话ID')
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='用户ID')
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment='对话标题')
    create_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间')
    update_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), comment='更新时间')
    yn: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'1'"), comment='是否启用')
    model_config_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='模型配置ID')

    model_config: Mapped[Optional['ModelConfig']] = relationship('ModelConfig', back_populates='conversation')
    message: Mapped[list['Message']] = relationship('Message', back_populates='conversation')


class Message(Base):
    __tablename__ = 'message'
    __table_args__ = (
        ForeignKeyConstraint(['conversation_id'], ['conversation.id'], ondelete='CASCADE', name='message_ibfk_1'),
        Index('idx_message_conversation_id', 'conversation_id'),
        {'comment': '消息'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='消息ID')
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='用户ID')
    conversation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='对话ID')
    role: Mapped[str] = mapped_column(String(20), nullable=False, comment='角色 (user/assistant/tool)')
    content: Mapped[str] = mapped_column(Text, nullable=False, comment='消息内容 (JSON 字符串)')
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='发送时间')
    yn: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'1'"), comment='是否启用')

    conversation: Mapped['Conversation'] = relationship('Conversation', back_populates='message')
