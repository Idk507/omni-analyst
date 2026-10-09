from __future__ import annotations

from omni_analyst.models.contracts import PolicyDecision, RiskTier, TaskContract


class PolicyEngine:
    """Minimal policy gate implementing v5 risk tier decisions."""

    def evaluate_task(self, task: TaskContract) -> PolicyDecision:
        policy_chain = [
            "risk_classification",
            "tool_permission_guard",
            "hitl_gate",
        ]
        if task.risk_tier == RiskTier.R3:
            return PolicyDecision(
                allowed=False,
                reason="R3 actions require human approval",
                risk_tier=task.risk_tier,
                requires_hitl=True,
                policy_chain=policy_chain,
            )
        if task.risk_tier == RiskTier.R2 and task.priority.value == "CRITICAL":
            return PolicyDecision(
                allowed=True,
                reason="Allowed with elevated monitoring",
                risk_tier=task.risk_tier,
                requires_hitl=False,
                policy_chain=policy_chain,
            )
        return PolicyDecision(
            allowed=True,
            reason="Allowed by baseline policy",
            risk_tier=task.risk_tier,
            requires_hitl=False,
            policy_chain=policy_chain,
        )

