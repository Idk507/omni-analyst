from __future__ import annotations

from typing import Dict, Iterable, List

from omni_analyst.models.contracts import DeepAgentTodo


class DeepTodoStore:
    def __init__(self) -> None:
        self._todos_by_run: dict[str, list[DeepAgentTodo]] = {}

    def write_todos(self, run_id: str, todos: Iterable[Dict[str, str]]) -> Dict[str, object]:
        parsed = [DeepAgentTodo(**todo) for todo in todos]
        self._todos_by_run[run_id] = parsed
        return {
            "run_id": run_id,
            "todos": [todo.model_dump(mode="json") for todo in parsed],
            "count": len(parsed),
        }

    def list_todos(self, run_id: str) -> List[DeepAgentTodo]:
        return list(self._todos_by_run.get(run_id, []))
