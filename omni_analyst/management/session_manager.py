from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Dict, List

from omni_analyst.models.contracts import ManagedAgentSession


class ManagedSessionStore:
    """Tracks agent sessions separately from task execution state."""

    def __init__(self) -> None:
        self._sessions: Dict[str, ManagedAgentSession] = {}
        self._lock = RLock()

    def create(self, session: ManagedAgentSession) -> ManagedAgentSession:
        with self._lock:
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str) -> ManagedAgentSession:
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Unknown managed session: {session_id}")
            return self._sessions[session_id]

    def list_sessions(self) -> List[ManagedAgentSession]:
        with self._lock:
            return list(self._sessions.values())

    def bind_subagents(
        self, session_id: str, subagent_ids: List[str]
    ) -> ManagedAgentSession:
        with self._lock:
            session = self.get(session_id)
            session.subagent_ids = list(dict.fromkeys(session.subagent_ids + subagent_ids))
            session.updated_at = datetime.now(timezone.utc)
            return session

    def bind_sandbox(self, session_id: str, sandbox_id: str) -> ManagedAgentSession:
        with self._lock:
            session = self.get(session_id)
            session.sandbox_id = sandbox_id
            session.updated_at = datetime.now(timezone.utc)
            return session

    def bind_browser(
        self, session_id: str, browser_session_id: str
    ) -> ManagedAgentSession:
        with self._lock:
            session = self.get(session_id)
            session.browser_session_id = browser_session_id
            session.updated_at = datetime.now(timezone.utc)
            return session
