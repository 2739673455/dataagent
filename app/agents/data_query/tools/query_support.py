"""只读 SQL 工具共享依赖与会话上下文"""

from uuid import UUID

from langchain.tools import ToolRuntime
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import AgentSessionKey, validate_agent_type
from app.conf.app_config import cfg
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.meta_pg_repo import MetaPGRepo
from app.services.authorization_service import AuthorizationService
from app.services.query_guard_service import QueryGuardService


def get_query_session(runtime: ToolRuntime) -> AgentSessionKey:
    """从工具运行配置中读取并校验数据查询 Session"""
    configurable = runtime.config.get("configurable", {})
    user_id = configurable.get("user_id")
    raw_conversation_id = configurable.get("conversation_id")
    analysis_id = configurable.get("analysis_id")
    raw_agent_type = configurable.get("agent_type")
    session_id = configurable.get("session_id")
    if not isinstance(user_id, int) or not isinstance(raw_conversation_id, str):
        raise TypeError("query context not found in config")
    if not isinstance(analysis_id, str) or not isinstance(session_id, str):
        raise TypeError("query specialist session not found in config")
    if not isinstance(raw_agent_type, str):
        raise TypeError("query agent type not found in config")
    agent_type = validate_agent_type(raw_agent_type)
    if agent_type != "data_query":
        raise ValueError("SQL tools require a data_query session")
    return AgentSessionKey(
        user_id=user_id,
        conversation_id=UUID(raw_conversation_id),
        analysis_id=analysis_id,
        agent_type=agent_type,
        session_id=session_id,
    )


def build_query_guard(session: AsyncSession) -> QueryGuardService:
    """使用同一 PostgreSQL 会话构造目录和授权查询守卫"""
    return QueryGuardService(
        MetaPGRepo(session),
        data_source=cfg.query.data_source,
        current_database=cfg.doris_query.database,
        max_cell_bytes=cfg.query.max_cell_bytes,
        policy_provider=AuthorizationService(AuthPGRepo(session)),
    )
