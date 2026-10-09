from __future__ import annotations

# pylint: disable=import-error

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import api.main as api_main
from omni_analyst.cache.semantic_cache import SemanticCache
from omni_analyst.deep_agents.backends import StateBackend
from omni_analyst.deep_agents.permissions import (
    FilesystemPermissionEvaluator,
    PermissionDenied,
)
from omni_analyst.deep_agents.runtime import DeepAgentHarness
from omni_analyst.deep_agents.store import SQLiteDeepAgentStore
from omni_analyst.memory.fabric import MemoryFabric
from omni_analyst.middleware.runtime import build_default_middleware_stack
from omni_analyst.models.contracts import (
    DeepAgentRequest,
    FilesystemPermissionMode,
    FilesystemPermissionRule,
    ModelMessage,
    ModelResponse,
)
from omni_analyst.skills.runtime import SkillRuntime
from omni_analyst.time_travel.checkpoint import CheckpointStore
from omni_analyst.tool_gateway.service import ToolGateway


class ScriptedModelClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    def invoke(self, request: Any) -> ModelResponse:
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return ModelResponse(
            provider_id=request.provider_id,
            model=request.model or "scripted",
            content=self.responses[index],
        )


class DeepAgentUnitTests(unittest.TestCase):
    def test_permissions_are_first_match_wins(self) -> None:
        permissions = FilesystemPermissionEvaluator(
            [
                FilesystemPermissionRule(
                    operations=["write"],
                    paths=["/workspace/.env"],
                    mode=FilesystemPermissionMode.DENY,
                ),
                FilesystemPermissionRule(
                    operations=["write"],
                    paths=["/workspace/**"],
                    mode=FilesystemPermissionMode.ALLOW,
                ),
            ]
        )
        self.assertTrue(permissions.allowed("write", "/workspace/app.py"))
        with self.assertRaises(PermissionDenied):
            permissions.assert_allowed("write", "/workspace/.env")

    def test_state_backend_file_operations(self) -> None:
        backend = StateBackend()
        backend.write_file("notes/todo.txt", "alpha\nbeta")
        self.assertEqual(backend.read_file("notes/todo.txt")["line_count"], 2)
        backend.edit_file("notes/todo.txt", "beta", "done")
        self.assertIn("done", backend.read_file("notes/todo.txt")["content"])
        self.assertEqual(backend.grep("done")[0]["path"], "notes/todo.txt")

    def test_deep_agent_harness_runs_tool_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tool_call = json.dumps(
                {
                    "tool_calls": [
                        {
                            "tool": "deep:write_file",
                            "arguments": {"path": "out.txt", "content": "hello"},
                        }
                    ]
                }
            )
            harness = self._harness(temp_dir, [tool_call, "Final answer"])
            run = harness.run(
                DeepAgentRequest(
                    messages=[ModelMessage(role="user", content="write a file")],
                    workspace_root=temp_dir,
                    max_steps=2,
                )
            )
            self.assertEqual(run.status, "COMPLETED")
            self.assertEqual(run.final_answer, "Final answer")
            self.assertEqual((Path(temp_dir) / "out.txt").read_text(encoding="utf-8"), "hello")
            self.assertGreaterEqual(len(run.trace), 1)

    def test_deep_agent_interrupt_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tool_call = json.dumps(
                {
                    "tool_calls": [
                        {
                            "tool": "deep:write_file",
                            "arguments": {"path": "out.txt", "content": "hello"},
                        }
                    ]
                }
            )
            harness = self._harness(temp_dir, [tool_call])
            run = harness.run(
                DeepAgentRequest(
                    messages=[ModelMessage(role="user", content="write a file")],
                    workspace_root=temp_dir,
                    max_steps=1,
                    interrupt_on={"deep:write_file": True},
                )
            )
            self.assertEqual(run.status, "INTERRUPTED")
            resumed = harness.resume(run.run_id, [{"type": "approve"}])
            self.assertEqual(resumed.status, "COMPLETED")

    def test_async_subagent_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = self._harness(temp_dir, ["Final answer"])

            started = harness.start_async_subagent_task("researcher", "research async subagents")
            task_id = started["task_id"]
            checked = harness.check_async_subagent_task(task_id)
            listed = harness.list_async_subagent_tasks()

            self.assertEqual(checked["task_id"], task_id)
            self.assertIn(checked["status"], {"running", "success"})
            self.assertGreaterEqual(len(listed["tasks"]), 1)

    def test_deep_agent_can_start_async_subagent_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tool_call = json.dumps(
                {
                    "tool_calls": [
                        {
                            "tool": "deep:start_async_task",
                            "arguments": {"subagent": "researcher", "task": "gather facts"},
                        }
                    ]
                }
            )
            harness = self._harness(temp_dir, [tool_call, "Started background task"])
            run = harness.run(
                DeepAgentRequest(
                    messages=[ModelMessage(role="user", content="start background research")],
                    workspace_root=temp_dir,
                    max_steps=2,
                )
            )

            self.assertEqual(run.status, "COMPLETED")
            self.assertGreaterEqual(len(harness.list_async_subagent_tasks()["tasks"]), 1)

    def test_deep_agent_store_reloads_runs_and_async_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "deep.sqlite3"
            harness = self._harness(temp_dir, ["Final answer"], store=SQLiteDeepAgentStore(store_path))
            run = harness.run(
                DeepAgentRequest(
                    messages=[ModelMessage(role="user", content="hello")],
                    workspace_root=temp_dir,
                    max_steps=1,
                )
            )
            task = harness.start_async_subagent_task("researcher", "persist me")

            reloaded = self._harness(temp_dir, ["unused"], store=SQLiteDeepAgentStore(store_path))
            self.assertEqual(reloaded.get(run.run_id).run_id, run.run_id)
            self.assertTrue(
                any(item["task_id"] == task["task_id"] for item in reloaded.list_async_subagent_tasks()["tasks"])
            )

    def _harness(
        self,
        temp_dir: str,
        responses: list[str],
        store: SQLiteDeepAgentStore | None = None,
    ) -> DeepAgentHarness:
        return DeepAgentHarness(
            model_client=ScriptedModelClient(responses),  # type: ignore[arg-type]
            tool_gateway=ToolGateway(),
            memory_fabric=MemoryFabric(storage_path=Path(temp_dir) / "memory.json"),
            checkpoint_store=CheckpointStore(storage_path=Path(temp_dir) / "checkpoints.json"),
            skill_runtime=SkillRuntime(),
            middleware_stack=build_default_middleware_stack(),
            semantic_cache=SemanticCache(storage_path=Path(temp_dir) / "cache.json"),
            store=store,
        )


class DeepAgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(api_main.app)

    def test_deep_agent_api_runs_and_exposes_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = self.client.post(
                "/api/v5/deep-agents/run",
                json={
                    "messages": [{"role": "user", "content": "hello"}],
                    "provider_id": "local_echo",
                    "workspace_root": temp_dir,
                    "max_steps": 1,
                },
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "COMPLETED")
            trace = self.client.get(f"/api/v5/deep-agents/runs/{payload['run_id']}/trace")
            self.assertEqual(trace.status_code, 200)
            self.assertEqual(len(trace.json()), 1)

    def test_backend_inspect_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "a.txt").write_text("hello", encoding="utf-8")
            response = self.client.post(
                "/api/v5/deep-agents/backends/inspect",
                json={
                    "messages": [{"role": "user", "content": "inspect"}],
                    "workspace_root": temp_dir,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["root_entries"][0]["path"], "a.txt")

    def test_async_subagent_api_lifecycle(self) -> None:
        response = self.client.post(
            "/api/v5/deep-agents/async-subagents/tasks",
            json={"subagent": "researcher", "task": "collect background"},
        )
        self.assertEqual(response.status_code, 200)
        task_id = response.json()["task_id"]

        check = self.client.get(f"/api/v5/deep-agents/async-subagents/tasks/{task_id}")
        self.assertEqual(check.status_code, 200)
        self.assertEqual(check.json()["task_id"], task_id)

        listed = self.client.get("/api/v5/deep-agents/async-subagents/tasks")
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(len(listed.json()["tasks"]), 1)


if __name__ == "__main__":
    unittest.main()
