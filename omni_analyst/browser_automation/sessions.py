from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Dict, List

from omni_analyst.models.contracts import BrowserExecutionResult, BrowserSessionRecord


class BrowserSessionStore:
    """Tracks browser executions and artifacts by managed session."""

    def __init__(self, storage_path: str | Path | None = ".browser-artifacts/sessions.json") -> None:
        self._records: Dict[str, BrowserSessionRecord] = {}
        self._lock = RLock()
        self.storage_path = Path(storage_path) if storage_path else None
        self._load()

    def record_execution(
        self,
        *,
        managed_session_id: str | None,
        execution: BrowserExecutionResult,
    ) -> BrowserSessionRecord:
        with self._lock:
            key = managed_session_id or execution.execution_id
            record = self._records.get(key)
            if record is None:
                record = BrowserSessionRecord(
                    browser_session_id=key,
                    managed_session_id=managed_session_id,
                )
            record.latest_execution_id = execution.execution_id
            record.status = execution.status
            record.artifact_paths = list(
                dict.fromkeys(record.artifact_paths + execution.artifacts)
            )
            record.updated_at = datetime.now(timezone.utc)
            self._records[key] = record
            self._persist()
            return record

    def list_sessions(self) -> List[BrowserSessionRecord]:
        with self._lock:
            return list(self._records.values())

    def get(self, browser_session_id: str) -> BrowserSessionRecord:
        with self._lock:
            if browser_session_id not in self._records:
                raise KeyError(f"Unknown browser session: {browser_session_id}")
            return self._records[browser_session_id]

    def _load(self) -> None:
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            self._records = {
                item["browser_session_id"]: BrowserSessionRecord.model_validate(item)
                for item in payload.get("sessions", [])
            }
        except Exception:
            self._records = {}

    def _persist(self) -> None:
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessions": [
                record.model_dump(mode="json")
                for record in self._records.values()
            ]
        }
        self.storage_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
