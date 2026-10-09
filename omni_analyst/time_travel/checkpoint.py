from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List
from uuid import uuid4


@dataclass
class Checkpoint:
    checkpoint_id: str
    parent_id: str | None
    state: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CheckpointStore:
    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = Path(storage_path or ".omni_memory/checkpoints.json")
        self._checkpoints: dict[str, Checkpoint] = {}
        self._lock = RLock()
        self._load()

    def save(
        self,
        state: Dict[str, Any],
        *,
        parent_id: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> Checkpoint:
        with self._lock:
            if parent_id is not None and parent_id not in self._checkpoints:
                raise KeyError(f"Unknown parent checkpoint: {parent_id}")
            checkpoint = Checkpoint(
                checkpoint_id=f"checkpoint-{uuid4()}",
                parent_id=parent_id,
                state=deepcopy(state),
                metadata=metadata or {},
            )
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint
            self._persist()
            return deepcopy(checkpoint)

    def replay(self, checkpoint_id: str) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._get(checkpoint_id).state)

    def restore(self, checkpoint_id: str) -> Checkpoint:
        with self._lock:
            checkpoint = self._get(checkpoint_id)
            return self.save(
                checkpoint.state,
                parent_id=checkpoint_id,
                metadata={"restored_from": checkpoint_id},
            )

    def branch(
        self,
        checkpoint_id: str,
        updates: Dict[str, Any],
        *,
        metadata: Dict[str, Any] | None = None,
    ) -> Checkpoint:
        state = self.replay(checkpoint_id)
        self._deep_merge(state, updates)
        return self.save(state, parent_id=checkpoint_id, metadata=metadata or {"branch": True})

    def history(self) -> List[Checkpoint]:
        with self._lock:
            return sorted(
                (deepcopy(checkpoint) for checkpoint in self._checkpoints.values()),
                key=lambda checkpoint: checkpoint.created_at,
            )

    def lineage(self, checkpoint_id: str) -> List[Checkpoint]:
        with self._lock:
            lineage: list[Checkpoint] = []
            current: str | None = checkpoint_id
            while current is not None:
                checkpoint = self._get(current)
                lineage.append(deepcopy(checkpoint))
                current = checkpoint.parent_id
            return list(reversed(lineage))

    def children(self, checkpoint_id: str) -> List[Checkpoint]:
        with self._lock:
            return [
                deepcopy(checkpoint)
                for checkpoint in self._checkpoints.values()
                if checkpoint.parent_id == checkpoint_id
            ]

    def diff(self, left_id: str, right_id: str) -> Dict[str, Any]:
        left = self.replay(left_id)
        right = self.replay(right_id)
        return self._diff_dict(left, right)

    def prune(self, *, keep_last: int = 100) -> Dict[str, Any]:
        with self._lock:
            ordered = self.history()
            if len(ordered) <= keep_last:
                return {"removed": 0, "remaining": len(ordered)}
            keep_ids = {checkpoint.checkpoint_id for checkpoint in ordered[-keep_last:]}
            removed = 0
            for checkpoint_id in list(self._checkpoints.keys()):
                if checkpoint_id not in keep_ids:
                    del self._checkpoints[checkpoint_id]
                    removed += 1
            for checkpoint in self._checkpoints.values():
                if checkpoint.parent_id not in self._checkpoints:
                    checkpoint.parent_id = None
            self._persist()
            return {"removed": removed, "remaining": len(self._checkpoints)}

    def _get(self, checkpoint_id: str) -> Checkpoint:
        if checkpoint_id not in self._checkpoints:
            raise KeyError(f"Unknown checkpoint: {checkpoint_id}")
        return self._checkpoints[checkpoint_id]

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            self._checkpoints = {
                item["checkpoint_id"]: Checkpoint(**item)
                for item in payload.get("checkpoints", [])
            }
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            self._checkpoints = {}

    def _persist(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 2,
            "checkpoints": [
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "parent_id": checkpoint.parent_id,
                    "state": checkpoint.state,
                    "metadata": checkpoint.metadata,
                    "created_at": checkpoint.created_at,
                }
                for checkpoint in self.history()
            ],
        }
        temp_path = self.storage_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.storage_path)

    def _deep_merge(self, base: Dict[str, Any], updates: Dict[str, Any]) -> None:
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = deepcopy(value)

    def _diff_dict(self, left: Dict[str, Any], right: Dict[str, Any], path: str = "") -> Dict[str, Any]:
        added: dict[str, Any] = {}
        removed: dict[str, Any] = {}
        changed: dict[str, Any] = {}
        for key in sorted(set(left) | set(right)):
            key_path = f"{path}.{key}" if path else key
            if key not in left:
                added[key_path] = right[key]
            elif key not in right:
                removed[key_path] = left[key]
            elif isinstance(left[key], dict) and isinstance(right[key], dict):
                nested = self._diff_dict(left[key], right[key], key_path)
                added.update(nested["added"])
                removed.update(nested["removed"])
                changed.update(nested["changed"])
            elif left[key] != right[key]:
                changed[key_path] = {"from": left[key], "to": right[key]}
        return {"added": added, "removed": removed, "changed": changed}
