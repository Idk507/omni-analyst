from __future__ import annotations

from typing import List

from omni_analyst.management.environment import EnvironmentMonitor
from omni_analyst.management.registry import AgentRegistry, SubagentProfileRegistry
from omni_analyst.management.session_manager import ManagedSessionStore
from omni_analyst.models.contracts import (
    AgentCapacity,
    AgentHealth,
    AgentManagementSnapshot,
    AgentRecord,
    AgentState,
    ManagedAgentSession,
)


class AgentManagementPlane:
    """Coordinates managed agents, subagent profiles, sessions, and environment state."""

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        profile_registry: SubagentProfileRegistry,
        session_store: ManagedSessionStore,
        environment_monitor: EnvironmentMonitor,
    ) -> None:
        self.agent_registry = agent_registry
        self.profile_registry = profile_registry
        self.session_store = session_store
        self.environment_monitor = environment_monitor

    def bootstrap_default_agents(self) -> List[AgentRecord]:
        records: list[AgentRecord] = []
        for profile in self.profile_registry.list_profiles():
            record = AgentRecord(
                agent_id=profile.profile_id,
                agent_type=profile.role,
                provider="core_subagent",
                capabilities=list(profile.tools),
                permissions=dict(profile.permissions),
                health=AgentHealth(state=AgentState.READY),
                capacity=AgentCapacity(concurrency_limit=1, current_load=0),
            )
            records.append(self.agent_registry.register(record))
            self.agent_registry.set_state(record.agent_id, AgentState.READY)
        return records

    def assign_subagents(
        self,
        *,
        session_id: str,
        profile_ids: List[str],
    ) -> ManagedAgentSession:
        for profile_id in profile_ids:
            self.profile_registry.get(profile_id)
            try:
                self.agent_registry.get(profile_id)
            except KeyError:
                self.bootstrap_default_agents()
                break
        return self.session_store.bind_subagents(session_id, profile_ids)

    def snapshot(self, *, session_id: str | None = None) -> AgentManagementSnapshot:
        sessions = self.session_store.list_sessions()
        active_session = None
        if session_id:
            active_session = self.session_store.get(session_id)
        environment = self.environment_monitor.snapshot(
            session_id=session_id,
            sandbox_active=bool(active_session and active_session.sandbox_id),
            browser_active=bool(active_session and active_session.browser_session_id),
        )
        agents = self.agent_registry.list_agents()
        profiles = self.profile_registry.list_profiles()
        return AgentManagementSnapshot(
            agents=agents,
            subagent_profiles=profiles,
            sessions=sessions,
            environment=environment,
            summary={
                "agent_count": len(agents),
                "profile_count": len(profiles),
                "session_count": len(sessions),
                "ready_agents": sum(
                    1 for agent in agents if agent.health.state == AgentState.READY
                ),
            },
        )
