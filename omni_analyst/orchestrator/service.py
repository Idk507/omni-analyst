from __future__ import annotations

from typing import List
from uuid import UUID, uuid4

from omni_analyst.audit.store import AuditStore
from omni_analyst.hitl.store import HitlStore
from omni_analyst.models.contracts import (
    AuditEvent,
    HitlRequest,
    PolicyDecision,
    RiskTier,
    TaskContract,
    TaskState,
)
from omni_analyst.policy.engine import PolicyEngine
from omni_analyst.orchestrator.result_bus import TaskResultBus
from omni_analyst.scheduler.task_scheduler import TaskScheduler


class OrchestratorService:
    """Coordinates task submission, policy checks, and scheduler handoff."""

    def __init__(
        self,
        scheduler: TaskScheduler,
        policy_engine: PolicyEngine,
        audit_store: AuditStore,
        hitl_store: HitlStore,
        result_bus: TaskResultBus | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.policy_engine = policy_engine
        self.audit_store = audit_store
        self.hitl_store = hitl_store
        self.result_bus = result_bus or TaskResultBus()

    def _audit(
        self,
        *,
        event_type: str,
        task: TaskContract,
        payload: dict,
        risk_tier: RiskTier | None = None,
    ) -> None:
        trace_id = task.trace_id or f"trace-{uuid4()}"
        session_id = task.session_id or "session-unknown"
        if task.trace_id is None:
            task.trace_id = trace_id
        event = AuditEvent(
            event_id=f"evt-{uuid4()}",
            trace_id=trace_id,
            session_id=session_id,
            task_id=task.task_id,
            agent_id=task.assigned_to or task.created_by,
            event_type=event_type,
            risk_tier=risk_tier or task.risk_tier,
            payload=payload,
        )
        self.audit_store.append(event)

    def validate_task(self, task: TaskContract) -> PolicyDecision:
        return self.policy_engine.evaluate_task(task)

    def submit_task(self, task: TaskContract) -> TaskContract:
        decision = self.policy_engine.evaluate_task(task)
        self._audit(
            event_type="task_submission_received",
            task=task,
            payload={"policy_chain": decision.policy_chain},
        )
        if not decision.allowed:
            task.status = TaskState.PAUSED
            task.hitl_required = decision.requires_hitl
            task.output = {
                "blocked": True,
                "reason": decision.reason,
                "requires_hitl": decision.requires_hitl,
            }
            if decision.requires_hitl:
                self.hitl_store.create(
                    HitlRequest(
                        task_id=task.task_id,
                        trace_id=task.trace_id or f"trace-{uuid4()}",
                        session_id=task.session_id or "session-unknown",
                        reason=decision.reason,
                        risk_tier=task.risk_tier,
                        action_summary=f"{task.task_type} submitted by {task.created_by}",
                    )
                )
            self._audit(
                event_type="task_submission_blocked",
                task=task,
                payload=task.output,
                risk_tier=task.risk_tier,
            )
            return task
        queued_task = self.scheduler.add_task(task)
        self._audit(
            event_type="task_queued",
            task=queued_task,
            payload={"dependencies": [str(dep) for dep in queued_task.dependencies]},
        )
        return queued_task

    def dispatch_ready(self, limit: int = 4) -> List[TaskContract]:
        ready = self.scheduler.next_ready(limit=limit)
        for task in ready:
            self._audit(
                event_type="task_running",
                task=task,
                payload={"execution_class": task.execution_class.value},
            )
        return ready

    def complete_task(self, task_id: UUID, output: dict | None = None) -> TaskContract:
        task = self.scheduler.get_task(task_id)
        if output:
            task.output.update(output)
        receipts = self.result_bus.route(task)
        self.scheduler.mark_done(task_id)
        self._audit(event_type="task_result_routed", task=task, payload={"receipts": receipts})
        self._audit(event_type="task_done", task=task, payload=task.output)
        return task

    def fail_task(self, task_id: UUID, reason: str) -> TaskContract:
        task = self.scheduler.mark_failed(task_id, reason)
        self._audit(
            event_type="task_failed",
            task=task,
            payload={"reason": reason, "retry_count": task.retry_count},
        )
        if task.status == TaskState.RETRYING:
            self.scheduler.requeue_retrying(task_id)
            self._audit(
                event_type="task_requeued",
                task=task,
                payload={"reason": reason, "retry_count": task.retry_count},
            )
        return task

