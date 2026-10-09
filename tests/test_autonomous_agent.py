from __future__ import annotations

# pylint: disable=import-error

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import api.main as api_main
from omni_analyst.autonomous_agent.config import AutonomousConfigLoader
from omni_analyst.autonomous_agent.runtime import AutonomousAgentKernel
from omni_analyst.cache.semantic_cache import SemanticCache
from omni_analyst.deep_agents.runtime import DeepAgentHarness
from omni_analyst.memory.fabric import MemoryFabric
from omni_analyst.middleware.runtime import build_default_middleware_stack
from omni_analyst.models.contracts import AutonomousAgentRequest, ModelResponse
from omni_analyst.skills.creator import AutonomousSkillCreator
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


class AutonomousAgentUnitTests(unittest.TestCase):
    def test_config_loader_reads_profiles_and_instruction_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".omniagent").mkdir()
            (root / ".omniagent" / "config.json").write_text(
                json.dumps(
                    {
                        "provider_id": "local_echo",
                        "approval_policy": "never",
                        "profiles": {"dev": {"model": "fast-model"}},
                    }
                ),
                encoding="utf-8",
            )
            (root / "AGENTS.md").write_text("Use tests before final answers.", encoding="utf-8")
            loader = AutonomousConfigLoader()

            config = loader.load(root, profile="dev")
            instructions = loader.instruction_files(root)

            self.assertEqual(config.provider_id, "local_echo")
            self.assertEqual(config.model, "fast-model")
            self.assertEqual(config.approval_policy, "never")
            self.assertEqual(instructions[0]["content"], "Use tests before final answers.")

    def test_kernel_runs_deep_agent_writes_memory_and_creates_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "CLAUDE.md").write_text("Keep changes small.", encoding="utf-8")
            skill_runtime = SkillRuntime()
            kernel = AutonomousAgentKernel(
                deep_harness=DeepAgentHarness(
                    model_client=ScriptedModelClient(["Final autonomous answer"]),  # type: ignore[arg-type]
                    tool_gateway=ToolGateway(),
                    memory_fabric=MemoryFabric(storage_path=root / "memory.json"),
                    checkpoint_store=CheckpointStore(storage_path=root / "checkpoints.json"),
                    skill_runtime=skill_runtime,
                    middleware_stack=build_default_middleware_stack(),
                    semantic_cache=SemanticCache(storage_path=root / "cache.json"),
                ),
                skill_runtime=skill_runtime,
                skill_creator=AutonomousSkillCreator(skill_runtime=skill_runtime),
            )

            run = kernel.run(
                AutonomousAgentRequest(
                    prompt="build an autonomous helper",
                    workspace_root=temp_dir,
                    learn_from_run=True,
                )
            )

            self.assertEqual(run.status, "COMPLETED")
            self.assertIsNotNone(run.created_skill)
            self.assertTrue((root / ".omni_memory" / "memory").exists())
            self.assertGreaterEqual(len(kernel.memory_search(temp_dir, "autonomous")), 1)


class AutonomousAgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(api_main.app)

    def test_autonomous_agent_api_run_and_memory_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_response = self.client.post(
                "/api/v5/autonomous-agent/run",
                json={
                    "prompt": "summarize autonomous repo patterns",
                    "workspace_root": temp_dir,
                    "provider_id": "local_echo",
                    "max_steps": 1,
                    "learn_from_run": False,
                },
            )
            self.assertEqual(run_response.status_code, 200)
            payload = run_response.json()
            self.assertEqual(payload["status"], "COMPLETED")

            search_response = self.client.get(
                "/api/v5/autonomous-agent/memory/search",
                params={"workspace_root": temp_dir, "query": "autonomous"},
            )
            self.assertEqual(search_response.status_code, 200)
            self.assertGreaterEqual(len(search_response.json()), 1)

    def test_autonomous_agent_config_inspect_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "AGENTS.md").write_text("Project rules", encoding="utf-8")
            response = self.client.get(
                "/api/v5/autonomous-agent/config/inspect",
                params={"workspace_root": temp_dir},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["instruction_files"][0]["content"], "Project rules")


if __name__ == "__main__":
    unittest.main()
