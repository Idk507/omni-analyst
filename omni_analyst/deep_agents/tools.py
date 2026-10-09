from __future__ import annotations

from typing import Any, Dict

from omni_analyst.deep_agents.backends import VirtualFilesystemBackend
from omni_analyst.deep_agents.subagents import AsyncDeepSubagentRuntime, DeepSubagentRuntime
from omni_analyst.deep_agents.todos import DeepTodoStore
from omni_analyst.models.contracts import SandboxCommand, ToolRegistration
from omni_analyst.sandbox.executor import SandboxExecutor
from omni_analyst.tool_gateway.service import ToolGateway


class DeepAgentToolRegistrar:
    def __init__(
        self,
        *,
        gateway: ToolGateway,
        backend: VirtualFilesystemBackend,
        todo_store: DeepTodoStore,
        subagents: DeepSubagentRuntime,
        async_subagents: AsyncDeepSubagentRuntime | None = None,
        sandbox_executor: SandboxExecutor | None = None,
        sandbox_enabled: bool = False,
        run_id: str = "deep-run",
    ) -> None:
        self.gateway = gateway
        self.backend = backend
        self.todo_store = todo_store
        self.subagents = subagents
        self.async_subagents = async_subagents or AsyncDeepSubagentRuntime()
        self.sandbox_executor = sandbox_executor or SandboxExecutor()
        self.sandbox_enabled = sandbox_enabled
        self.run_id = run_id

    _TOOL_NAMES = [
        "deep:write_todos",
        "deep:ls",
        "deep:read_file",
        "deep:write_file",
        "deep:edit_file",
        "deep:glob",
        "deep:grep",
        "deep:execute",
        "deep:task",
        "deep:start_async_task",
        "deep:check_async_task",
        "deep:update_async_task",
        "deep:cancel_async_task",
        "deep:list_async_tasks",
    ]

    def register_all(self) -> None:
        self._register("deep:write_todos", self.write_todos, ["todos"])
        self._register("deep:ls", self.ls, ["path"])
        self._register("deep:read_file", self.read_file, ["path"])
        self._register("deep:write_file", self.write_file, ["path", "content"])
        self._register("deep:edit_file", self.edit_file, ["path", "old_string", "new_string"])
        self._register("deep:glob", self.glob, ["pattern"])
        self._register("deep:grep", self.grep, ["pattern"])
        self._register("deep:execute", self.execute, ["command"])
        self._register("deep:task", self.task, ["subagent", "task"])
        self._register("deep:start_async_task", self.start_async_task, ["subagent", "task"])
        self._register("deep:check_async_task", self.check_async_task, ["task_id"])
        self._register("deep:update_async_task", self.update_async_task, ["task_id", "instruction"])
        self._register("deep:cancel_async_task", self.cancel_async_task, ["task_id"])
        self._register("deep:list_async_tasks", self.list_async_tasks, [])

    def unregister_all(self) -> None:
        """Remove all deep-agent tools from the gateway after a run completes."""
        for name in self._TOOL_NAMES:
            self.gateway.unregister_tool(name)

    def write_todos(self, todos: list[dict[str, str]]) -> Dict[str, Any]:
        return self.todo_store.write_todos(self.run_id, todos)

    def ls(self, path: str = ".") -> Dict[str, Any]:
        return {"entries": self.backend.ls(path)}

    def read_file(self, path: str, offset: int = 0, limit: int | None = None) -> Dict[str, Any]:
        return self.backend.read_file(path, offset=offset, limit=limit)

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        return self.backend.write_file(path, content)

    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Dict[str, Any]:
        return self.backend.edit_file(path, old_string, new_string, replace_all=replace_all)

    def glob(self, pattern: str) -> Dict[str, Any]:
        return {"matches": self.backend.glob(pattern)}

    def grep(self, pattern: str, path: str = ".") -> Dict[str, Any]:
        return {"matches": self.backend.grep(pattern, path)}

    def execute(self, command: list[str], cwd: str | None = None, timeout_seconds: int = 30) -> Dict[str, Any]:
        if not self.sandbox_enabled:
            return {"ok": False, "error": "Sandbox execution is disabled for this run"}
        result = self.sandbox_executor.run(
            SandboxCommand(command=command, cwd=cwd, timeout_seconds=timeout_seconds)
        )
        return result.model_dump(mode="json")

    def task(self, subagent: str, task: str) -> Dict[str, Any]:
        return self.subagents.run_task(subagent, task)

    def start_async_task(self, subagent: str, task: str) -> Dict[str, Any]:
        return self.async_subagents.start_task(subagent, task)

    def check_async_task(self, task_id: str) -> Dict[str, Any]:
        return self.async_subagents.check_task(task_id)

    def update_async_task(self, task_id: str, instruction: str) -> Dict[str, Any]:
        return self.async_subagents.update_task(task_id, instruction)

    def cancel_async_task(self, task_id: str) -> Dict[str, Any]:
        return self.async_subagents.cancel_task(task_id)

    def list_async_tasks(self) -> Dict[str, Any]:
        return self.async_subagents.list_tasks()

    def _register(self, name: str, handler, required: list[str]) -> None:
        self.gateway.register_local_tool(
            name,
            handler,
            metadata=ToolRegistration(
                tool_name=name,
                source="deep_agents",
                namespace="deep",
                input_schema={"required": required},
                timeout_seconds=60,
            ),
        )
