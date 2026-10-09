from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Dict, List
from uuid import uuid4

from omni_analyst.models.contracts import (
    AsyncDeepSubagentSpec,
    AsyncSubagentStatus,
    AsyncSubagentTask,
    DeepSubagentSpec,
)
from omni_analyst.deep_agents.store import SQLiteDeepAgentStore


class DeepSubagentRuntime:
    """Delegates isolated tasks to registered subagent specs.

    The default implementation is deterministic and returns a concise report.
    DeepAgentHarness can inject a callable that runs a nested harness when needed.
    """

    def __init__(self, runner: Callable[[DeepSubagentSpec, str], Dict[str, Any]] | None = None) -> None:
        self._specs: dict[str, DeepSubagentSpec] = {}
        self._runner = runner

    def register(self, spec: DeepSubagentSpec) -> DeepSubagentSpec:
        self._specs[spec.name] = spec
        return spec

    def list_subagents(self) -> List[DeepSubagentSpec]:
        return sorted(self._specs.values(), key=lambda spec: spec.name)

    def run_task(self, name: str, task: str) -> Dict[str, Any]:
        if name not in self._specs:
            raise KeyError(f"Unknown subagent: {name}")
        spec = self._specs[name]
        if self._runner is not None:
            return self._runner(spec, task)
        return {
            "subagent": spec.name,
            "task": task,
            "status": "completed",
            "report": f"{spec.name} reviewed task with isolated context.",
            "tools": spec.tools,
        }


class AsyncDeepSubagentRuntime:
    """Tracks background subagent tasks outside supervisor message history.

    This mirrors LangChain Deep Agents async subagents locally: launch returns a
    stable task ID immediately, while check/update/cancel/list operate on a
    durable task channel owned by the supervisor runtime.
    """

    def __init__(
        self,
        runner: Callable[[AsyncDeepSubagentSpec, str, List[str]], Dict[str, Any]] | None = None,
        *,
        max_workers: int = 8,
        store: SQLiteDeepAgentStore | None = None,
    ) -> None:
        self._specs: dict[str, AsyncDeepSubagentSpec] = {}
        self._runner = runner
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="async-subagent")
        self._tasks: dict[str, AsyncSubagentTask] = {}
        self._futures: dict[str, Future[Dict[str, Any]]] = {}
        self._cancel_requested: set[str] = set()
        self._lock = Lock()
        self.store = store
        if self.store is not None:
            self._tasks.update({task.task_id: task for task in self.store.list_async_tasks()})

    def register(self, spec: AsyncDeepSubagentSpec) -> AsyncDeepSubagentSpec:
        self._specs[spec.name] = spec
        return spec

    def list_subagents(self) -> List[AsyncDeepSubagentSpec]:
        return sorted(self._specs.values(), key=lambda spec: spec.name)

    def start_task(self, agent_name: str, task: str) -> Dict[str, Any]:
        if agent_name not in self._specs:
            raise KeyError(f"Unknown async subagent: {agent_name}")
        spec = self._specs[agent_name]
        task_record = AsyncSubagentTask(
            agent_name=agent_name,
            graph_id=spec.graph_id,
            thread_id=f"thread-{uuid4()}",
            run_id=f"run-{uuid4()}",
            task=task,
            status=AsyncSubagentStatus.RUNNING,
            started_at=self._now(),
        )
        with self._lock:
            self._tasks[task_record.task_id] = task_record
            self._persist_task(task_record)
        future = self._executor.submit(self._execute, task_record.task_id, spec, task, [])
        with self._lock:
            self._futures[task_record.task_id] = future
        return self.check_task(task_record.task_id)

    def check_task(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._task(task_id)
            future = self._futures.get(task_id)
            if future is not None and future.done() and task.status == AsyncSubagentStatus.RUNNING:
                try:
                    task.result = future.result()
                    task.status = AsyncSubagentStatus.SUCCESS
                except Exception as exc:  # pragma: no cover - defensive thread boundary
                    task.error = str(exc)
                    task.status = AsyncSubagentStatus.ERROR
                task.completed_at = self._now()
            task.last_checked_at = self._now()
            self._persist_task(task)
            return task.model_dump(mode="json")

    def update_task(self, task_id: str, instruction: str) -> Dict[str, Any]:
        with self._lock:
            task = self._task(task_id)
            if task.status in {
                AsyncSubagentStatus.SUCCESS,
                AsyncSubagentStatus.ERROR,
                AsyncSubagentStatus.CANCELLED,
            }:
                raise ValueError(f"Cannot update terminal async task: {task_id}")
            task.updates.append(instruction)
            task.last_updated_at = self._now()
            spec = self._specs[task.agent_name]
            task.run_id = f"run-{uuid4()}"
            self._cancel_requested.add(task_id)
            self._persist_task(task)
        replacement = self._executor.submit(self._execute, task_id, spec, task.task, task.updates)
        with self._lock:
            self._cancel_requested.discard(task_id)
            self._futures[task_id] = replacement
        return self.check_task(task_id)

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._task(task_id)
            future = self._futures.get(task_id)
            self._cancel_requested.add(task_id)
            if future is not None:
                future.cancel()
            task.status = AsyncSubagentStatus.CANCELLED
            task.completed_at = self._now()
            task.last_checked_at = self._now()
            self._persist_task(task)
            return task.model_dump(mode="json")

    def list_tasks(self) -> Dict[str, Any]:
        task_ids = list(self._tasks)
        return {"tasks": [self.check_task(task_id) for task_id in task_ids]}

    def _execute(
        self,
        task_id: str,
        spec: AsyncDeepSubagentSpec,
        task: str,
        updates: List[str],
    ) -> Dict[str, Any]:
        if task_id in self._cancel_requested:
            return {"cancelled": True}
        if self._runner is not None:
            return self._runner(spec, task, updates)
        return {
            "subagent": spec.name,
            "graph_id": spec.graph_id,
            "task": task,
            "updates": updates,
            "status": "completed",
            "report": f"{spec.name} completed async task with isolated state.",
            "tools": spec.tools,
            "transport": "http" if spec.url else "local",
        }

    def _task(self, task_id: str) -> AsyncSubagentTask:
        if task_id not in self._tasks:
            raise KeyError(f"Unknown async subagent task: {task_id}")
        return self._tasks[task_id]

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _persist_task(self, task: AsyncSubagentTask) -> None:
        if self.store is not None:
            self.store.save_async_task(task)
