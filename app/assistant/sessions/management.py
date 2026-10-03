"""专业 Agent Session 的查询与资源删除。"""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.assistant.contracts import (
    DeleteSessionRequest,
    DeleteSessionResult,
    ListSessionsResult,
    SessionSummary,
)
from app.assistant.execution.activity import SessionActivity
from app.assistant.models.session import AgentSessionKey
from app.assistant.repositories.session import SessionCheckpointRepository
from app.assistant.sessions.checkpoint_view import SpecialistCheckpointView
from app.assistant.sessions.control import SessionControl
from app.assistant.sessions.identity import (
    session_checkpoint_namespace,
)
from app.sandbox import DockerSandboxManager
from app.sandbox.contracts import SandboxSessionScope
from app.shared.contracts.analysis import (
    IDENTIFIER_PATTERN,
    AgentType,
    validate_agent_type,
)


class AgentSessionService:
    """专业 Session 的目录查询、状态汇总和资源删除。"""

    def __init__(
        self,
        *,
        session_store: SessionCheckpointRepository,
        control: SessionControl,
        activity: SessionActivity,
        sandbox: DockerSandboxManager,
        user_id: int,
        conversation_id: UUID,
    ) -> None:
        """绑定当前用户会话的状态、互斥控制和沙箱资源。"""
        self._session_store = session_store
        self._control = control
        self._activity = activity
        self._sandbox = sandbox
        self._user_id = user_id
        self._conversation_id = conversation_id

    async def list_sessions(self, analysis_id: str | None) -> ListSessionsResult:
        """查询当前 Conversation 内的专业 Agent Session。"""
        namespaces = await self._session_store.list_namespaces(analysis_id)
        active_sessions = {
            session_checkpoint_namespace(key): at
            for key, at in self._activity.snapshot(
                self._user_id, self._conversation_id
            ).items()
        }
        all_namespaces = set(namespaces)
        for namespace in active_sessions:
            session_key = self._parse_session_namespace(namespace)
            if session_key is not None and (
                analysis_id is None or session_key.analysis_id == analysis_id
            ):
                all_namespaces.add(namespace)

        async def load_summary(checkpoint_ns: str) -> SessionSummary | None:
            """读取单个 Session 的最新持久化状态并叠加活跃状态。"""
            session_key = self._parse_session_namespace(checkpoint_ns)
            if session_key is None or (
                analysis_id is not None and session_key.analysis_id != analysis_id
            ):
                return None
            state = await self._session_store.read_state(session_key)
            active_at = active_sessions.get(checkpoint_ns)
            if state.updated_at is None and active_at is None:
                return None
            updated_at = state.updated_at
            if active_at is not None:
                updated_at = active_at
            view = SpecialistCheckpointView(state.values)
            return view.session_summary(
                session_key,
                active=active_at is not None,
                updated_at=updated_at,
            )

        summaries = await asyncio.gather(
            *(load_summary(namespace) for namespace in sorted(all_namespaces))
        )
        sessions = sorted(
            (summary for summary in summaries if summary is not None),
            key=lambda item: (item.analysis_id, item.agent_type, item.session_id),
        )
        return ListSessionsResult(analysis_id=analysis_id, sessions=sessions)

    async def delete_session(
        self,
        request: DeleteSessionRequest,
    ) -> DeleteSessionResult:
        """幂等删除专业 Agent Session 的持久化与沙箱状态。"""
        session_key = self._build_session_key(
            request.analysis_id,
            request.agent_type,
            request.session_id,
        )
        async with (
            self._control.lock(session_key),
        ):
            try:
                checkpoint_deleted = await self._session_store.delete_checkpoint(
                    session_key
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RuntimeError("删除 Session Checkpoint 失败") from exc
            try:
                workspace_deleted = await self._sandbox.delete_session(
                    self._user_id,
                    self._conversation_id,
                    SandboxSessionScope(
                        session_key.analysis_id,
                        session_key.agent_type,
                        session_key.session_id,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RuntimeError("删除 Session 工作区失败") from exc
        existed = checkpoint_deleted or workspace_deleted
        return DeleteSessionResult(
            analysis_id=request.analysis_id,
            agent_type=request.agent_type,
            session_id=request.session_id,
            existed=existed,
            message=("Session 已删除" if existed else "Session 不存在，无需删除"),
        )

    def _parse_session_namespace(self, checkpoint_ns: str) -> AgentSessionKey | None:
        """把受控专业 Session namespace 还原为身份键。"""
        parts = checkpoint_ns.split("/")
        if (
            len(parts) != 4
            or parts[0] != "subagents"
            or IDENTIFIER_PATTERN.fullmatch(parts[1]) is None
            or IDENTIFIER_PATTERN.fullmatch(parts[3]) is None
        ):
            return None
        try:
            return AgentSessionKey(
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                analysis_id=parts[1],
                agent_type=validate_agent_type(parts[2]),
                session_id=parts[3],
            )
        except ValueError:
            return None

    def _build_session_key(
        self,
        analysis_id: str,
        agent_type: AgentType,
        session_id: str,
    ) -> AgentSessionKey:
        """把受控标识绑定到当前用户会话。"""
        return AgentSessionKey(
            user_id=self._user_id,
            conversation_id=self._conversation_id,
            analysis_id=analysis_id,
            agent_type=agent_type,
            session_id=session_id,
        )
