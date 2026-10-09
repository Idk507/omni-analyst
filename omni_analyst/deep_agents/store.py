from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from omni_analyst.models.contracts import AsyncSubagentTask, DeepAgentRun


class SQLiteDeepAgentStore:
    """Durable local store for agent runs and async task metadata."""

    def __init__(self, storage_path: str | Path = ".omni_memory/deep_agents.sqlite3") -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def save_run(self, run: DeepAgentRun) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO deep_agent_runs(run_id, status, payload, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.status.value,
                json.dumps(run.model_dump(mode="json")),
                (run.completed_at or run.created_at).isoformat(),
            ),
        )

    def get_run(self, run_id: str) -> DeepAgentRun | None:
        row = self._fetchone("SELECT payload FROM deep_agent_runs WHERE run_id = ?", (run_id,))
        if row is None:
            return None
        return DeepAgentRun.model_validate(json.loads(row[0]))

    def list_runs(self) -> List[DeepAgentRun]:
        rows = self._fetchall("SELECT payload FROM deep_agent_runs ORDER BY created_at, run_id")
        return [DeepAgentRun.model_validate(json.loads(row[0])) for row in rows]

    def save_async_task(self, task: AsyncSubagentTask) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO async_subagent_tasks(task_id, agent_name, status, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.agent_name,
                task.status.value,
                json.dumps(task.model_dump(mode="json")),
                (task.last_checked_at or task.completed_at or task.started_at or task.created_at).isoformat(),
            ),
        )

    def get_async_task(self, task_id: str) -> AsyncSubagentTask | None:
        row = self._fetchone("SELECT payload FROM async_subagent_tasks WHERE task_id = ?", (task_id,))
        if row is None:
            return None
        return AsyncSubagentTask.model_validate(json.loads(row[0]))

    def list_async_tasks(self) -> List[AsyncSubagentTask]:
        rows = self._fetchall("SELECT payload FROM async_subagent_tasks ORDER BY created_at, task_id")
        return [AsyncSubagentTask.model_validate(json.loads(row[0])) for row in rows]

    def _ensure_schema(self) -> None:
        connection = sqlite3.connect(self.storage_path)
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deep_agent_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS async_subagent_tasks (
                    task_id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        connection = sqlite3.connect(self.storage_path)
        try:
            connection.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        connection = sqlite3.connect(self.storage_path)
        try:
            row = connection.execute(sql, params).fetchone()
            connection.commit()
            return row
        finally:
            connection.close()

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> List[tuple[Any, ...]]:
        connection = sqlite3.connect(self.storage_path)
        try:
            rows = connection.execute(sql, params).fetchall()
            connection.commit()
            return rows
        finally:
            connection.close()
