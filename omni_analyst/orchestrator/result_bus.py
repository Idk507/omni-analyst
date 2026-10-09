from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from omni_analyst.models.contracts import ResultRoute, TaskContract


class TaskResultBus:
    """Routes task outputs to configured destinations."""

    def __init__(self, root: str | Path = ".omni_memory/result_bus") -> None:
        self.root = Path(root)
        self.memory_events: list[dict[str, Any]] = []
        self.graph_events: list[dict[str, Any]] = []
        self.consensus_events: list[dict[str, Any]] = []
        self.stream_events: list[dict[str, Any]] = []

    def route(self, task: TaskContract) -> List[Dict[str, Any]]:
        receipts: list[dict[str, Any]] = []
        for route in task.result_routes:
            if route == ResultRoute.ORCHESTRATOR:
                receipts.append({"route": route.value, "status": "retained"})
            elif route == ResultRoute.MEMORY:
                self.memory_events.append({"task_id": str(task.task_id), "output": task.output})
                receipts.append({"route": route.value, "status": "stored", "count": len(self.memory_events)})
            elif route == ResultRoute.FILE:
                path = self._write_file(task)
                receipts.append({"route": route.value, "status": "written", "path": str(path)})
            elif route == ResultRoute.GRAPH:
                self.graph_events.append({"task_id": str(task.task_id), "graph": task.output.get("graph", task.output)})
                receipts.append({"route": route.value, "status": "queued", "count": len(self.graph_events)})
            elif route == ResultRoute.CONSENSUS:
                self.consensus_events.append({"task_id": str(task.task_id), "output": task.output})
                receipts.append({"route": route.value, "status": "queued", "count": len(self.consensus_events)})
            elif route == ResultRoute.STREAM:
                self.stream_events.append({"task_id": str(task.task_id), "output": task.output})
                receipts.append({"route": route.value, "status": "published", "count": len(self.stream_events)})
        task.output["result_bus_receipts"] = receipts
        return receipts

    def snapshot(self) -> Dict[str, Any]:
        return {
            "memory_events": len(self.memory_events),
            "graph_events": len(self.graph_events),
            "consensus_events": len(self.consensus_events),
            "stream_events": len(self.stream_events),
            "root": str(self.root),
        }

    def _write_file(self, task: TaskContract) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{task.task_id}.json"
        path.write_text(json.dumps(task.output, indent=2, default=str), encoding="utf-8")
        return path
