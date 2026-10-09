from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set
from uuid import UUID

from omni_analyst.models.contracts import TaskContract, TaskPriority, TaskState


@dataclass(order=True)
class _PriorityKey:
    value: int


_PRIORITY_ORDER = {
    TaskPriority.CRITICAL: 0,
    TaskPriority.HIGH: 1,
    TaskPriority.NORMAL: 2,
    TaskPriority.LOW: 3,
    TaskPriority.BACKGROUND: 4,
}


class TaskScheduler:
    """Dependency-aware scheduler for v5 task DAG execution."""

    def __init__(self) -> None:
        self._tasks: Dict[UUID, TaskContract] = {}
        self._deps: Dict[UUID, Set[UUID]] = defaultdict(set)
        self._reverse_deps: Dict[UUID, Set[UUID]] = defaultdict(set)

    def add_task(self, task: TaskContract) -> TaskContract:
        self._tasks[task.task_id] = task
        self._deps[task.task_id] = set(task.dependencies)
        for dep in task.dependencies:
            self._reverse_deps[dep].add(task.task_id)
        task.status = TaskState.QUEUED
        return task

    def get_task(self, task_id: UUID) -> TaskContract:
        return self._tasks[task_id]

    def list_tasks(self) -> List[TaskContract]:
        return list(self._tasks.values())

    def mark_done(self, task_id: UUID) -> None:
        task = self._tasks[task_id]
        task.status = TaskState.DONE
        task.completed_at = datetime.now(timezone.utc)
        for child in self._reverse_deps.get(task_id, set()):
            self._deps[child].discard(task_id)

    def mark_paused(self, task_id: UUID) -> None:
        task = self._tasks[task_id]
        task.status = TaskState.PAUSED

    def mark_failed(self, task_id: UUID, reason: str) -> TaskContract:
        task = self._tasks[task_id]
        task.output["error"] = reason
        if task.retry_count < task.retry_policy.max_retries:
            task.retry_count += 1
            task.status = TaskState.RETRYING
        else:
            task.status = TaskState.FAILED
            task.completed_at = datetime.now(timezone.utc)
        return task

    def requeue_retrying(self, task_id: UUID) -> TaskContract:
        task = self._tasks[task_id]
        if task.status != TaskState.RETRYING:
            return task
        task.status = TaskState.QUEUED
        return task

    def next_ready(self, limit: int = 1) -> List[TaskContract]:
        ready = [
            t
            for t in self._tasks.values()
            if t.status == TaskState.QUEUED and len(self._deps[t.task_id]) == 0
        ]
        ready.sort(key=lambda t: _PriorityKey(_PRIORITY_ORDER[t.priority]))
        selected = ready[:limit]
        for task in selected:
            task.status = TaskState.RUNNING
            if task.started_at is None:
                task.started_at = datetime.now(timezone.utc)
        return selected

    def lease_ready(self, worker_id: str, limit: int = 1, lease_seconds: int = 300) -> List[TaskContract]:
        leased = self.next_ready(limit=limit)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        for task in leased:
            task.output.setdefault("worker", {})
            task.output["worker"].update(
                {
                    "worker_id": worker_id,
                    "lease_expires_at": expires_at.isoformat(),
                }
            )
        return leased

    def recover_expired_leases(self, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        recovered = 0
        for task in self._tasks.values():
            worker = task.output.get("worker", {})
            expires = worker.get("lease_expires_at")
            if task.status != TaskState.RUNNING or not expires:
                continue
            if datetime.fromisoformat(expires) < current:
                task.status = TaskState.QUEUED
                task.output.setdefault("worker_history", []).append(worker)
                task.output.pop("worker", None)
                recovered += 1
        return recovered

    def snapshot(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for task in self._tasks.values():
            counts[task.status.value] += 1
        return dict(counts)

