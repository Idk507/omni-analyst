from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import api.main as api_main
from omni_analyst.browser_automation.executor import PlaywrightAutomationExecutor
from omni_analyst.browser_automation.planner import PlaywrightAutomationPlanner
from omni_analyst.browser_automation.sessions import BrowserSessionStore
from omni_analyst.code_workflow.pipeline import CodeWorkflowPipeline
from omni_analyst.management.environment import EnvironmentMonitor
from omni_analyst.management.registry import build_default_subagent_profiles
from omni_analyst.models.contracts import (
    BrowserAutomationRequest,
    BrowserExecutionRequest,
    CodePipelineRequest,
    SandboxCommand,
)
from omni_analyst.sandbox.executor import SandboxExecutor


class AgentManagementTests(unittest.TestCase):
    def test_default_subagent_profiles_include_code_roles(self) -> None:
        registry = build_default_subagent_profiles()
        profile_ids = {profile.profile_id for profile in registry.list_profiles()}
        self.assertIn("code_builder", profile_ids)
        self.assertIn("code_developer", profile_ids)
        self.assertIn("code_reviewer", profile_ids)
        self.assertIn("code_issue_fixer", profile_ids)
        self.assertIn("code_tester", profile_ids)
        self.assertIn("browser_automation_agent", profile_ids)

    def test_environment_monitor_reports_missing_tools(self) -> None:
        snapshot = EnvironmentMonitor().snapshot(
            required_tools=["python", "definitely-not-a-real-tool"]
        )
        self.assertEqual(snapshot.health, "DEGRADED")
        self.assertTrue(snapshot.warnings)

    def test_sandbox_executor_merges_custom_env(self) -> None:
        result = SandboxExecutor().run(
            SandboxCommand(
                command=["python", "-c", "import os; print(os.getenv('OMNI_TEST_FLAG'))"],
                env={"OMNI_TEST_FLAG": "enabled"},
            )
        )
        self.assertTrue(result.evaluation["passed"])
        self.assertIn("enabled", result.stdout)


class CodeWorkflowTests(unittest.TestCase):
    def test_code_pipeline_runs_sandboxed_tests(self) -> None:
        pipeline = CodeWorkflowPipeline(
            profile_registry=build_default_subagent_profiles(),
            sandbox_executor=SandboxExecutor(),
        )
        run = pipeline.run(
            CodePipelineRequest(
                goal="verify this repository",
                repository_path=".",
                test_commands=[["python", "-c", "print('ok')"]],
            )
        )
        self.assertEqual(run.status, "DONE")
        stage_names = [stage.name for stage in run.stages]
        self.assertEqual(
            stage_names,
            ["builder", "developer", "reviewer", "fixer", "tester"],
        )
        reviewer = run.stages[2]
        fixer = run.stages[3]
        tester = run.stages[-1]
        self.assertEqual(reviewer.findings, [])
        self.assertEqual(fixer.output["fix_actions"][0]["type"], "no_op")
        self.assertTrue(tester.sandbox_results[0].evaluation["passed"])

    def test_code_pipeline_requires_verified_fix_when_sandbox_fails(self) -> None:
        pipeline = CodeWorkflowPipeline(
            profile_registry=build_default_subagent_profiles(),
            sandbox_executor=SandboxExecutor(),
        )
        run = pipeline.run(
            CodePipelineRequest(
                goal="verify failing change",
                repository_path=".",
                test_commands=[
                    [
                        "python",
                        "-c",
                        "import sys; print('reproduced failure'); sys.exit(1)",
                    ]
                ],
            )
        )
        self.assertEqual(run.status, "FAILED")
        reviewer = run.stages[2]
        fixer = run.stages[3]
        tester = run.stages[4]
        self.assertEqual(reviewer.status, "NEEDS_FIX")
        self.assertTrue(reviewer.findings[0]["verified"])
        self.assertEqual(fixer.status, "NEEDS_MANUAL_PATCH")
        self.assertEqual(
            fixer.output["fix_actions"][0]["type"],
            "targeted_fix_required",
        )
        self.assertEqual(tester.status, "FAILED")

    def test_code_pipeline_applies_scoped_patch_and_verifies_fix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "demo"
            tests = root / "tests"
            package.mkdir()
            tests.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "calculator.py").write_text(
                "def add(a, b):\n    return a - b\n",
                encoding="utf-8",
            )
            (tests / "test_calculator.py").write_text(
                "from demo.calculator import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                encoding="utf-8",
            )
            pipeline = CodeWorkflowPipeline(
                profile_registry=build_default_subagent_profiles(),
                sandbox_executor=SandboxExecutor(),
            )
            run = pipeline.run(
                CodePipelineRequest(
                    goal="Fix calculator.add so it returns the sum.",
                    repository_path=str(root),
                    changed_files=["demo/calculator.py"],
                    test_commands=[["python", "-m", "pytest", "tests/test_calculator.py"]],
                    metadata={
                        "fix_patches": [
                            {
                                "type": "replace_text",
                                "path": "demo/calculator.py",
                                "old_text": "return a - b",
                                "new_text": "return a + b",
                            }
                        ]
                    },
                )
            )
            self.assertEqual(run.status, "FIXED_AND_TESTED")
            fixer = next(stage for stage in run.stages if stage.name == "fixer")
            tester = run.stages[-1]
            self.assertEqual(fixer.output["patch_result"]["changed_files"], ["demo/calculator.py"])
            self.assertIn("return a + b", (package / "calculator.py").read_text(encoding="utf-8"))
            self.assertEqual(tester.status, "DONE")
            self.assertTrue(tester.sandbox_results[0].evaluation["passed"])

    def test_code_pipeline_blocks_sensitive_patch_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("SECRET=value\n", encoding="utf-8")
            pipeline = CodeWorkflowPipeline(
                profile_registry=build_default_subagent_profiles(),
                sandbox_executor=SandboxExecutor(),
            )
            run = pipeline.run(
                CodePipelineRequest(
                    goal="Never edit secrets.",
                    repository_path=str(root),
                    changed_files=[".env"],
                    test_commands=[["python", "-c", "import sys; sys.exit(1)"]],
                    metadata={
                        "fix_patches": [
                            {
                                "type": "write_file",
                                "path": ".env",
                                "content": "SECRET=changed\n",
                            }
                        ]
                    },
                )
            )
            fixer = run.stages[3]
            self.assertEqual(fixer.status, "NEEDS_MANUAL_PATCH")
            self.assertTrue(fixer.output["patch_result"]["rollback_performed"])
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "SECRET=value\n")


class BrowserPlannerTests(unittest.TestCase):
    def test_browser_planner_blocks_login_as_manual_checkpoint(self) -> None:
        plan = PlaywrightAutomationPlanner().plan(
            BrowserAutomationRequest(
                goal="login to the app",
                url="https://example.test",
                page_snapshot="textbox Password",
            )
        )
        self.assertEqual(plan.perception_mode, "snapshot")
        self.assertEqual(plan.actions[1]["type"], "manual_checkpoint")

    def test_browser_planner_creates_click_plan(self) -> None:
        plan = PlaywrightAutomationPlanner().plan(
            BrowserAutomationRequest(
                goal="click 'Reports'",
                url="https://example.test/dashboard",
                page_snapshot="button Reports",
            )
        )
        self.assertEqual(plan.actions[0]["type"], "navigate")
        self.assertEqual(plan.actions[1]["type"], "click_by_text")
        self.assertEqual(plan.actions[1]["target_text"], "Reports")


class BrowserExecutorTests(unittest.TestCase):
    def test_browser_executor_dry_run_executes_plan_without_browser(self) -> None:
        plan = PlaywrightAutomationPlanner().plan(
            BrowserAutomationRequest(
                goal="click 'Reports'",
                url="https://example.test/dashboard",
                page_snapshot="button Reports",
            )
        )
        result = PlaywrightAutomationExecutor().execute(
            BrowserExecutionRequest(plan=plan, dry_run=True)
        )
        self.assertEqual(result.status, "DONE")
        self.assertEqual(result.mode, "dry_run")
        self.assertTrue(result.evaluation["passed"])
        self.assertGreaterEqual(result.evaluation["verified_steps"], 1)
        self.assertIsNotNone(result.steps[0].page_snapshot)

    def test_browser_planner_adds_assertion_and_sessions_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = PlaywrightAutomationPlanner().plan(
                BrowserAutomationRequest(
                    goal="click 'Reports'",
                    url="https://example.test/dashboard",
                    page_snapshot="button Reports",
                    constraints={"assert_text": "Reports"},
                )
            )
            self.assertEqual(plan.actions[-1]["type"], "assert_text")
            result = PlaywrightAutomationExecutor().execute(
                BrowserExecutionRequest(plan=plan, dry_run=True, session_id="browser-persist")
            )
            store_path = Path(temp_dir) / "sessions.json"
            store = BrowserSessionStore(storage_path=store_path)
            store.record_execution(managed_session_id="browser-persist", execution=result)
            reloaded = BrowserSessionStore(storage_path=store_path)
            self.assertEqual(reloaded.get("browser-persist").latest_execution_id, result.execution_id)

    def test_browser_executor_blocks_manual_checkpoint(self) -> None:
        plan = PlaywrightAutomationPlanner().plan(
            BrowserAutomationRequest(
                goal="login to the app",
                url="https://example.test",
                page_snapshot="textbox Password",
            )
        )
        result = PlaywrightAutomationExecutor().execute(
            BrowserExecutionRequest(plan=plan, dry_run=True)
        )
        self.assertEqual(result.status, "BLOCKED")
        self.assertFalse(result.evaluation["passed"])
        self.assertTrue(result.evaluation["blocked"])

    def test_browser_executor_policy_blocks_r3_execution(self) -> None:
        plan = PlaywrightAutomationPlanner().plan(
            BrowserAutomationRequest(
                goal="delete all records",
                url="https://example.test/admin",
            )
        )
        result = PlaywrightAutomationExecutor().execute(
            BrowserExecutionRequest(plan=plan, dry_run=False)
        )
        self.assertEqual(result.status, "BLOCKED")
        self.assertTrue(result.evaluation["requires_hitl"])


class AgentManagementApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(api_main.app)

    def test_subagent_profiles_endpoint(self) -> None:
        response = self.client.get("/api/v5/subagents/profiles")
        self.assertEqual(response.status_code, 200)
        profile_ids = {profile["profile_id"] for profile in response.json()}
        self.assertIn("code_reviewer", profile_ids)

    def test_managed_session_endpoint(self) -> None:
        response = self.client.post(
            "/api/v5/sessions/managed",
            json={
                "session_id": "managed-test",
                "user_goal": "ship code safely",
                "subagent_ids": ["code_builder", "code_tester"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session_id"], "managed-test")

    def test_bootstrap_and_management_snapshot_endpoints(self) -> None:
        bootstrap = self.client.post("/api/v5/agents/bootstrap-defaults")
        self.assertEqual(bootstrap.status_code, 200)
        self.assertGreaterEqual(len(bootstrap.json()), 5)

        snapshot = self.client.get("/api/v5/management/snapshot")
        self.assertEqual(snapshot.status_code, 200)
        self.assertGreaterEqual(snapshot.json()["summary"]["profile_count"], 5)
        self.assertGreaterEqual(snapshot.json()["summary"]["agent_count"], 5)

    def test_assign_subagents_to_managed_session(self) -> None:
        self.client.post(
            "/api/v5/sessions/managed",
            json={
                "session_id": "managed-assign-test",
                "user_goal": "delegate work",
            },
        )
        response = self.client.post(
            "/api/v5/sessions/managed/managed-assign-test/subagents",
            json={"profile_ids": ["code_builder", "code_reviewer"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["subagent_ids"],
            ["code_builder", "code_reviewer"],
        )

    def test_browser_plan_endpoint(self) -> None:
        response = self.client.post(
            "/api/v5/browser/plan",
            json={
                "goal": "click 'Settings'",
                "url": "https://example.test",
                "page_snapshot": "button Settings",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["actions"][1]["type"], "click_by_text")

    def test_browser_execute_endpoint_dry_run(self) -> None:
        plan_response = self.client.post(
            "/api/v5/browser/plan",
            json={
                "goal": "click 'Settings'",
                "url": "https://example.test",
                "page_snapshot": "button Settings",
            },
        )
        response = self.client.post(
            "/api/v5/browser/execute",
            json={"plan": plan_response.json(), "dry_run": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "DONE")
        self.assertEqual(response.json()["mode"], "dry_run")

    def test_browser_execute_binds_managed_browser_session(self) -> None:
        self.client.post(
            "/api/v5/sessions/managed",
            json={
                "session_id": "browser-managed-test",
                "user_goal": "run browser action",
            },
        )
        plan_response = self.client.post(
            "/api/v5/browser/plan",
            json={
                "goal": "click 'Settings'",
                "url": "https://example.test",
                "page_snapshot": "button Settings",
            },
        )
        execute = self.client.post(
            "/api/v5/browser/execute",
            json={
                "plan": plan_response.json(),
                "dry_run": True,
                "session_id": "browser-managed-test",
            },
        )
        self.assertEqual(execute.status_code, 200)
        managed = self.client.get("/api/v5/sessions/managed/browser-managed-test")
        self.assertEqual(managed.json()["browser_session_id"], "browser-managed-test")

        browser_sessions = self.client.get("/api/v5/browser/sessions")
        self.assertTrue(
            any(
                session["browser_session_id"] == "browser-managed-test"
                for session in browser_sessions.json()
            )
        )
        browser_session = self.client.get("/api/v5/browser/sessions/browser-managed-test")
        self.assertEqual(browser_session.status_code, 200)
        self.assertEqual(browser_session.json()["browser_session_id"], "browser-managed-test")


if __name__ == "__main__":
    unittest.main()
