from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable


@dataclass
class SLODefinition:
    name: str
    sli: str
    target: float
    window_seconds: int = 3600
    comparator: str = ">="
    runbook_id: str | None = None


@dataclass
class SLIRecord:
    name: str
    value: float
    labels: dict[str, Any] = field(default_factory=dict)
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AlertRecord:
    alert_id: str
    slo: str
    sli: str
    value: float
    target: float
    severity: str
    message: str
    runbook_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved: bool = False


@dataclass
class Runbook:
    runbook_id: str
    title: str
    steps: list[str]
    automation: str | None = None


class ObservabilityRuntime:
    """SLI/SLO tracking, alerting, and runbook automation for local and worker runtimes."""

    def __init__(self, alert_hook: Callable[[AlertRecord], None] | None = None) -> None:
        self._sli_records: list[SLIRecord] = []
        self._slos: dict[str, SLODefinition] = {}
        self._alerts: dict[str, AlertRecord] = {}
        self._runbooks: dict[str, Runbook] = {}
        self._alert_hook = alert_hook
        self._bootstrap_defaults()

    def record_sli(self, name: str, value: float, labels: dict[str, Any] | None = None) -> dict[str, Any]:
        record = SLIRecord(name=name, value=value, labels=labels or {})
        self._sli_records.append(record)
        alerts = self.evaluate()
        return {"record": record.__dict__, "alerts": [alert.__dict__ for alert in alerts]}

    def define_slo(self, slo: SLODefinition) -> SLODefinition:
        self._slos[slo.name] = slo
        return slo

    def register_runbook(self, runbook: Runbook) -> Runbook:
        self._runbooks[runbook.runbook_id] = runbook
        return runbook

    def evaluate(self) -> list[AlertRecord]:
        triggered: list[AlertRecord] = []
        for slo in self._slos.values():
            values = [record.value for record in self._sli_records if record.name == slo.sli]
            if not values:
                continue
            current = mean(values[-20:])
            if self._meets(current, slo):
                continue
            alert_id = f"{slo.name}:{len(self._alerts) + 1}"
            if alert_id in self._alerts:
                continue
            alert = AlertRecord(
                alert_id=alert_id,
                slo=slo.name,
                sli=slo.sli,
                value=current,
                target=slo.target,
                severity="critical" if abs(current - slo.target) > 0.25 else "warning",
                message=f"SLO {slo.name} breached: {slo.sli}={current:.3f}, target {slo.comparator} {slo.target:.3f}",
                runbook_id=slo.runbook_id,
            )
            self._alerts[alert_id] = alert
            if self._alert_hook:
                self._alert_hook(alert)
            triggered.append(alert)
        return triggered

    def runbook(self, runbook_id: str) -> dict[str, Any]:
        runbook = self._runbooks[runbook_id]
        return {
            "runbook": runbook.__dict__,
            "automation_result": self._automation(runbook),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "sli_count": len(self._sli_records),
            "slos": [slo.__dict__ for slo in self._slos.values()],
            "alerts": [alert.__dict__ for alert in self._alerts.values()],
            "runbooks": [runbook.__dict__ for runbook in self._runbooks.values()],
        }

    def _meets(self, value: float, slo: SLODefinition) -> bool:
        if slo.comparator == "<=":
            return value <= slo.target
        if slo.comparator == "<":
            return value < slo.target
        if slo.comparator == ">":
            return value > slo.target
        return value >= slo.target

    def _automation(self, runbook: Runbook) -> dict[str, Any]:
        if runbook.automation == "recover_worker_leases":
            return {"action": "recover_worker_leases", "status": "ready"}
        if runbook.automation == "inspect_cache":
            return {"action": "inspect_cache", "status": "ready"}
        return {"action": runbook.automation or "manual", "status": "manual"}

    def _bootstrap_defaults(self) -> None:
        self.register_runbook(
            Runbook(
                runbook_id="worker-lease-recovery",
                title="Recover expired worker leases",
                steps=[
                    "Check worker heartbeat and scheduler lease age.",
                    "Recover expired leases.",
                    "Re-queue blocked tasks and inspect repeated failures.",
                ],
                automation="recover_worker_leases",
            )
        )
        self.register_runbook(
            Runbook(
                runbook_id="cache-latency-triage",
                title="Triage semantic cache latency",
                steps=[
                    "Inspect semantic cache backend stats.",
                    "Check Redis/vector adapter connectivity if enabled.",
                    "Switch to local SQLite/JSON backend if external cache is degraded.",
                ],
                automation="inspect_cache",
            )
        )
        self.define_slo(SLODefinition(name="query_acceptance", sli="query_acceptance_rate", target=0.99, comparator=">="))
        self.define_slo(SLODefinition(name="worker_recovery", sli="worker_recovery_success", target=0.95, comparator=">=", runbook_id="worker-lease-recovery"))
        self.define_slo(SLODefinition(name="cache_latency", sli="semantic_cache_latency_ms", target=250.0, comparator="<=", runbook_id="cache-latency-triage"))
