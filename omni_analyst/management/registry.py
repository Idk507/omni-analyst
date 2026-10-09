from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Dict, List

from omni_analyst.models.contracts import AgentRecord, AgentState, SubagentProfile


class AgentRegistry:
    """In-memory agent registry implementing v5 lifecycle transitions."""

    def __init__(self) -> None:
        self._records: Dict[str, AgentRecord] = {}
        self._lock = RLock()

    def register(self, record: AgentRecord) -> AgentRecord:
        with self._lock:
            record.health.state = AgentState.REGISTERED
            self._records[record.agent_id] = record
            return record

    def deregister(self, agent_id: str) -> AgentRecord:
        with self._lock:
            record = self.get(agent_id)
            record.health.state = AgentState.OFFLINE
            return self._records.pop(agent_id)

    def list_agents(self) -> List[AgentRecord]:
        with self._lock:
            return list(self._records.values())

    def get(self, agent_id: str) -> AgentRecord:
        with self._lock:
            if agent_id not in self._records:
                raise KeyError(f"Unknown agent_id: {agent_id}")
            return self._records[agent_id]

    def set_state(self, agent_id: str, state: AgentState) -> AgentRecord:
        with self._lock:
            record = self.get(agent_id)
            record.health.state = state
            return record

    def heartbeat(self, agent_id: str) -> AgentRecord:
        with self._lock:
            record = self.get(agent_id)
            record.health.last_heartbeat = datetime.now(timezone.utc)
            return record

    def update_permissions(self, agent_id: str, permissions: dict) -> AgentRecord:
        with self._lock:
            record = self.get(agent_id)
            record.permissions = permissions
            return record

    def update_load(self, agent_id: str, current_load: int) -> AgentRecord:
        with self._lock:
            record = self.get(agent_id)
            record.capacity.current_load = current_load
            if record.health.state != AgentState.DRAINING:
                record.health.state = (
                    AgentState.BUSY if current_load > 0 else AgentState.READY
                )
            return record


class SubagentProfileRegistry:
    """Stores reusable role-scoped subagent profiles."""

    def __init__(self) -> None:
        self._profiles: Dict[str, SubagentProfile] = {}
        self._lock = RLock()

    def register(self, profile: SubagentProfile) -> SubagentProfile:
        with self._lock:
            self._profiles[profile.profile_id] = profile
            return profile

    def get(self, profile_id: str) -> SubagentProfile:
        with self._lock:
            if profile_id not in self._profiles:
                raise KeyError(f"Unknown profile_id: {profile_id}")
            return self._profiles[profile_id]

    def list_profiles(self) -> List[SubagentProfile]:
        with self._lock:
            return list(self._profiles.values())


def build_default_subagent_profiles() -> SubagentProfileRegistry:
    registry = SubagentProfileRegistry()
    for profile in [
        SubagentProfile(
            profile_id="code_builder",
            name="Code Builder",
            role="builder",
            description="Turns requirements into a concrete implementation plan and patch outline.",
            system_prompt="Design the smallest safe implementation plan before coding.",
            tools=["repo_read", "sandbox_eval"],
            permissions={"write": False, "network": False},
        ),
        SubagentProfile(
            profile_id="code_developer",
            name="Code Developer",
            role="developer",
            description="Implements the selected plan in the repository.",
            system_prompt="Apply scoped code changes and preserve unrelated user work.",
            tools=["repo_read", "repo_write", "sandbox_eval"],
            permissions={"write": True, "network": False},
        ),
        SubagentProfile(
            profile_id="code_reviewer",
            name="Code Reviewer",
            role="reviewer",
            description="Finds correctness, security, and regression risks.",
            system_prompt="Review changes for real bugs first; avoid style-only findings.",
            tools=["repo_read", "sandbox_eval"],
            permissions={"write": False, "network": False},
        ),
        SubagentProfile(
            profile_id="code_issue_fixer",
            name="Code Issue Fixer",
            role="fixer",
            description="Fixes verified review findings.",
            system_prompt="Fix only verified issues and rerun the relevant tests.",
            tools=["repo_read", "repo_write", "sandbox_eval"],
            permissions={"write": True, "network": False},
        ),
        SubagentProfile(
            profile_id="code_tester",
            name="Code Tester",
            role="tester",
            description="Runs sandboxed tests and evaluates outputs after fixes.",
            system_prompt="Execute tests in a sandbox and classify failures precisely.",
            tools=["sandbox_eval"],
            permissions={"write": False, "network": False},
        ),
        SubagentProfile(
            profile_id="browser_automation_agent",
            name="Browser Automation Agent",
            role="browser_operator",
            description="Plans Playwright actions from screenshots, URLs, or page snapshots.",
            system_prompt="Use perception, action, and verification loops for browser automation.",
            tools=["playwright"],
            permissions={"browser": True, "network": True},
        ),
        SubagentProfile(
            profile_id="environment_monitor",
            name="Environment Monitor",
            role="environment_monitor",
            description="Tracks workspace, sandbox, and browser session health.",
            system_prompt="Detect environment drift, missing tools, and unsafe session state.",
            tools=["env_snapshot"],
            permissions={"write": False, "network": False},
        ),
        SubagentProfile(
            profile_id="session_manager",
            name="Session Manager",
            role="session_manager",
            description="Tracks managed sessions, subagent assignments, and sandbox bindings.",
            system_prompt="Preserve session continuity and isolate subagent context.",
            tools=["session_store"],
            permissions={"write": True, "network": False},
        ),
    ]:
        registry.register(profile)
    return registry

