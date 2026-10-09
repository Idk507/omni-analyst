from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Dict, List
from uuid import UUID

from omni_analyst.models.contracts import HitlDecision, HitlRequest


class HitlStore:
    """Tracks approval requests for blocked or high-risk actions."""

    def __init__(self) -> None:
        self._requests: Dict[UUID, HitlRequest] = {}
        self._lock = RLock()

    def create(self, request: HitlRequest) -> HitlRequest:
        with self._lock:
            self._requests[request.hitl_id] = request
            return request

    def get(self, hitl_id: UUID) -> HitlRequest:
        with self._lock:
            if hitl_id not in self._requests:
                raise KeyError(f"Unknown hitl_id: {hitl_id}")
            return self._requests[hitl_id]

    def list_requests(self) -> List[HitlRequest]:
        with self._lock:
            return list(self._requests.values())

    def resolve(
        self, hitl_id: UUID, decision: HitlDecision, reviewer: str, note: str | None = None
    ) -> HitlRequest:
        with self._lock:
            request = self.get(hitl_id)
            request.status = decision
            request.reviewer = reviewer
            request.resolution_note = note
            request.resolved_at = datetime.now(timezone.utc)
            return request

