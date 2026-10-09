from __future__ import annotations

# pylint: disable=import-error

import json
import tempfile
import unittest
from pathlib import Path
from typing import List

from omni_analyst.hooks.runtime import HookRuntime
from omni_analyst.live_agent.runner import LiveAgentRunner
from omni_analyst.live_agent.tools import extract_action
from omni_analyst.live_agent.workspace import WorkspaceManager
from omni_analyst.middleware.runtime import build_default_middleware_stack
from omni_analyst.models.contracts import ModelMessage, ModelRequest, ModelResponse
from omni_analyst.sandbox.executor import SandboxExecutor


class StubModelClient:
    """Returns scripted JSON action responses turn-by-turn."""

    def __init__(self, scripted_actions: List[dict]) -> None:
        self.scripted_actions = list(scripted_actions)
        self.calls: List[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if not self.scripted_actions:
            payload = {"thought": "no more actions", "action": "final_answer", "args": {"text": "done"}}
        else:
            payload = self.scripted_actions.pop(0)
        return ModelResponse(
            provider_id=request.provider_id,
            model=request.model or "stub",
            content="```json\n" + json.dumps(payload) + "\n```",
            raw={"stub": True},
            usage={},
        )


class ExtractActionTests(unittest.TestCase):
    def test_extracts_fenced_json(self) -> None:
        text = "Here you go:\n```json\n{\"action\": \"final_answer\", \"args\": {\"text\": \"ok\"}}\n```"
        action = extract_action(text)
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action["action"], "final_answer")

    def test_extracts_plain_json(self) -> None:
        action = extract_action('{"action":"write_file","args":{"path":"a.txt","content":"hi"}}')
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action["action"], "write_file")

    def test_returns_none_when_no_action(self) -> None:
        self.assertIsNone(extract_action("just prose, no JSON"))


class LiveAgentRunnerTests(unittest.TestCase):
    def test_agent_writes_file_runs_python_and_serves_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspaces_root = Path(temp_dir) / "ws"
            workspace_manager = WorkspaceManager(root=str(workspaces_root))
            scripted = [
                {
                    "thought": "create a static landing page",
                    "action": "write_file",
                    "args": {
                        "path": "index.html",
                        "content": "<!doctype html><html><body><h1>Hello OmniAgent</h1></body></html>",
                    },
                },
                {
                    "thought": "run a quick python check",
                    "action": "run_python",
                    "args": {"code": "print('python-ran')"},
                },
                {
                    "thought": "expose the preview",
                    "action": "serve_preview",
                    "args": {"entry": "index.html"},
                },
                {
                    "thought": "wrap up",
                    "action": "final_answer",
                    "args": {"text": "Static demo created successfully."},
                },
            ]
            stub = StubModelClient(scripted)
            runner = LiveAgentRunner(
                model_client=stub,  # type: ignore[arg-type]
                sandbox_executor=SandboxExecutor(),
                workspace_manager=workspace_manager,
                hook_runtime=HookRuntime(),
                middleware_stack=build_default_middleware_stack(),
            )
            result = runner.run(query="Build a hello-world static page", provider_id="local_echo")
            self.assertEqual(result.status, "DONE")
            self.assertIn("Static demo created", result.final_text)
            self.assertTrue(result.preview_url and result.preview_url.endswith("/index.html"))
            self.assertGreaterEqual(result.iterations, 4)
            file_paths = {item["path"] for item in result.artifacts}
            self.assertIn("index.html", file_paths)
            workspace_dir = workspaces_root / result.workspace_id
            self.assertTrue((workspace_dir / "index.html").exists())
            tool_titles = [event["title"] for event in result.timeline if event["kind"] == "tool"]
            self.assertTrue(any("write_file" in title for title in tool_titles))
            self.assertTrue(any("run_python" in title for title in tool_titles))

    def test_agent_recovers_from_unparseable_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_manager = WorkspaceManager(root=str(Path(temp_dir) / "ws"))

            class ChattyClient:
                def __init__(self) -> None:
                    self.calls = 0

                def invoke(self, request: ModelRequest) -> ModelResponse:
                    self.calls += 1
                    if self.calls == 1:
                        body = "I'm thinking out loud and forgot the JSON."
                    else:
                        body = "```json\n{\"action\": \"final_answer\", \"args\": {\"text\": \"recovered\"}}\n```"
                    return ModelResponse(
                        provider_id=request.provider_id,
                        model=request.model or "stub",
                        content=body,
                        raw={},
                        usage={},
                    )

            runner = LiveAgentRunner(
                model_client=ChattyClient(),  # type: ignore[arg-type]
                sandbox_executor=SandboxExecutor(),
                workspace_manager=workspace_manager,
                hook_runtime=HookRuntime(),
                middleware_stack=build_default_middleware_stack(),
            )
            result = runner.run(query="Test resilience", provider_id="local_echo")
            self.assertEqual(result.final_text, "recovered")


if __name__ == "__main__":
    unittest.main()
