from __future__ import annotations

import hashlib
import json
from threading import RLock
from typing import List, Optional

from omni_analyst.models.contracts import AuditEvent


class AuditStore:
    """Append-only audit log with lightweight hash chaining."""

    def __init__(self) -> None:
        self._events: List[AuditEvent] = []
        self._lock = RLock()

    def append(self, event: AuditEvent) -> AuditEvent:
        with self._lock:
            previous_hash = self._events[-1].payload_hash if self._events else None
            payload_blob = json.dumps(
                {
                    "event_id": event.event_id,
                    "trace_id": event.trace_id,
                    "session_id": event.session_id,
                    "task_id": str(event.task_id) if event.task_id else None,
                    "agent_id": event.agent_id,
                    "event_type": event.event_type,
                    "risk_tier": event.risk_tier.value,
                    "timestamp": event.timestamp.isoformat(),
                    "payload": event.payload,
                    "previous_event_hash": previous_hash,
                },
                sort_keys=True,
            ).encode("utf-8")
            event.previous_event_hash = previous_hash
            event.payload_hash = hashlib.sha256(payload_blob).hexdigest()
            self._events.append(event)
            return event

    def list_events(
        self,
        *,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> List[AuditEvent]:
        with self._lock:
            events = list(self._events)
        if session_id:
            events = [event for event in events if event.session_id == session_id]
        if trace_id:
            events = [event for event in events if event.trace_id == trace_id]
        if event_type:
            events = [event for event in events if event.event_type == event_type]
        return events

