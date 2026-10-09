from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

from omni_analyst.code_workflow.patcher import CodePatchEngine
from omni_analyst.management.registry import SubagentProfileRegistry
from omni_analyst.model_providers.client import ModelProviderClient, ModelProviderError
from omni_analyst.models.contracts import (
    CodePipelineRequest,
    CodePipelineRun,
    CodePipelineStage,
    ModelMessage,
    ModelRequest,
    SandboxCommand,
    SandboxResult,
)
from omni_analyst.sandbox.executor import SandboxExecutor


PIPELINE_PROFILES = [
    ("builder", "code_builder"),
    ("developer", "code_developer"),
    ("reviewer", "code_reviewer"),
    ("fixer", "code_issue_fixer"),
    ("tester", "code_tester"),
]


class CodeWorkflowPipeline:
    """Claude-style code pipeline with isolated role stages and sandbox eval."""

    def __init__(
        self,
        *,
        profile_registry: SubagentProfileRegistry,
        sandbox_executor: SandboxExecutor | None = None,
        patch_engine: CodePatchEngine | None = None,
        model_client: ModelProviderClient | None = None,
        max_fix_attempts: int = 2,
    ) -> None:
        self.profile_registry = profile_registry
        self.sandbox_executor = sandbox_executor or SandboxExecutor()
        self.patch_engine = patch_engine or CodePatchEngine()
        self.model_client = model_client
        self.max_fix_attempts = max_fix_attempts

    def run(self, request: CodePipelineRequest) -> CodePipelineRun:
        run = CodePipelineRun(request=request, status="RUNNING")

        builder = self._run_builder(request)
        run.stages.append(builder)

        developer = self._run_developer(request, builder)
        run.stages.append(developer)

        reviewer = self._run_reviewer(request, developer)
        run.stages.append(reviewer)

        fixer = self._run_fixer(request, reviewer)
        run.stages.append(fixer)

        tester = self._run_tester(request, fixer)
        run.stages.append(tester)

        attempts = 1
        while tester.status == "FAILED" and attempts < self.max_fix_attempts:
            retry_fixer = self._run_fixer(request, tester, attempt=attempts + 1)
            run.stages.append(retry_fixer)
            tester = self._run_tester(request, retry_fixer, attempt=attempts + 1)
            run.stages.append(tester)
            attempts += 1

        run.status = self._final_status(run.stages)
        run.completed_at = datetime.now(timezone.utc)
        return run

    def _run_builder(self, request: CodePipelineRequest) -> CodePipelineStage:
        stage = self._new_stage("builder", "code_builder")
        stage.output = {
            "goal": request.goal,
            "context_policy": "isolated_builder_context",
            "implementation_plan": [
                "Map the requested behavior to the smallest safe code change.",
                "Identify files likely to change before developer handoff.",
                "Define acceptance checks and sandbox commands.",
                "Apply scoped patch operations only inside changed_files.",
                "Review unified diffs before running sandbox verification.",
            ],
            "acceptance_checks": self._test_commands(request),
            "handoff": {
                "to": "code_developer",
                "requires": ["scoped_patch", "changed_file_manifest"],
            },
        }
        stage.status = "DONE"
        return stage

    def _run_developer(
        self, request: CodePipelineRequest, builder: CodePipelineStage
    ) -> CodePipelineStage:
        stage = self._new_stage("developer", "code_developer")
        stage.output = {
            "context_policy": "isolated_developer_context",
            "permissions_verified": self._permissions_allow("code_developer", write=True),
            "builder_handoff_received": builder.output.get("handoff", {}),
            "implementation_scope": request.changed_files,
            "developer_contract": {
                "must_preserve_unrelated_changes": True,
                "must_add_or_update_tests_when_behavior_changes": True,
                "must_leave_reviewable_diff": True,
            },
        }
        developer_patches = self._metadata_operations(request, "patches")
        if developer_patches:
            result = self.patch_engine.apply(
                request.repository_path,
                developer_patches,
                allowed_files=request.changed_files,
            )
            stage.output["patch_result"] = result.as_dict()
            stage.status = "DONE" if result.applied else "FAILED"
        else:
            stage.output["patch_result"] = {
                "applied": False,
                "reason": "No developer patch operations supplied.",
            }
            stage.status = "DONE"
        return stage

    def _run_reviewer(
        self, request: CodePipelineRequest, developer: CodePipelineStage
    ) -> CodePipelineStage:
        stage = self._new_stage("reviewer", "code_reviewer")
        stage.sandbox_results = self._run_sandbox_tests(request, phase="review")
        stage.findings = self._verified_findings_from_results(stage.sandbox_results)
        stage.output = {
            "context_policy": "isolated_reviewer_context",
            "permissions_verified": self._permissions_allow("code_reviewer", write=False),
            "developer_handoff_received": developer.output.get("developer_contract", {}),
            "parallel_review_agents": [
                {"name": "logic_reviewer", "focus": "correctness and edge cases"},
                {"name": "security_reviewer", "focus": "unsafe inputs and side effects"},
                {"name": "regression_reviewer", "focus": "compatibility and tests"},
            ],
            "verification_rule": "Only findings reproduced by sandbox output are passed to fixer.",
        }
        stage.status = "NEEDS_FIX" if stage.findings else "DONE"
        return stage

    def _run_fixer(
        self, request: CodePipelineRequest, reviewer: CodePipelineStage, *, attempt: int = 1
    ) -> CodePipelineStage:
        stage = self._new_stage("fixer", "code_issue_fixer")
        verified_findings = [
            finding for finding in reviewer.findings if finding.get("verified") is True
        ]
        fix_operations = self._fix_operations(request, verified_findings, attempt=attempt)
        patch_result = None
        if verified_findings and fix_operations:
            patch_result = self.patch_engine.apply(
                request.repository_path,
                fix_operations,
                allowed_files=request.changed_files,
            )
        stage.findings = verified_findings
        stage.output = {
            "context_policy": "isolated_fixer_context",
            "permissions_verified": self._permissions_allow("code_issue_fixer", write=True),
            "attempt": attempt,
            "verified_findings_received": len(verified_findings),
            "fix_policy": "Fix only verified findings, then trigger post-fix sandbox tests.",
            "fix_actions": self._fix_actions(verified_findings, patch_result.as_dict() if patch_result else None),
            "patch_result": patch_result.as_dict() if patch_result else {"applied": False},
            "handoff": {
                "to": "code_tester",
                "requires": ["post_fix_test_run", "failure_classification"],
            },
        }
        if not verified_findings:
            stage.status = "DONE"
        elif patch_result and patch_result.applied:
            stage.status = "FIX_APPLIED"
        else:
            stage.status = "NEEDS_MANUAL_PATCH"
        return stage

    def _run_tester(
        self, request: CodePipelineRequest, fixer: CodePipelineStage, *, attempt: int = 1
    ) -> CodePipelineStage:
        stage = self._new_stage("tester", "code_tester")
        stage.sandbox_results = self._run_sandbox_tests(request, phase="post_fix")
        stage.findings = self._verified_findings_from_results(stage.sandbox_results)
        stage.output = {
            "context_policy": "isolated_tester_context",
            "permissions_verified": self._permissions_allow("code_tester", write=False),
            "attempt": attempt,
            "fixer_handoff_received": fixer.output.get("handoff", {}),
            "post_tool_use_hook_equivalent": {
                "trigger": "after_fixer_stage",
                "commands": self._test_commands(request),
                "timeout_seconds": 120,
            },
        }
        stage.status = "FAILED" if stage.findings else "DONE"
        return stage

    def _new_stage(self, stage_name: str, profile_id: str) -> CodePipelineStage:
        profile = self.profile_registry.get(profile_id)
        return CodePipelineStage(
            name=stage_name,
            agent_id=profile.profile_id,
            status="RUNNING",
            output={
                "role": profile.role,
                "tools": profile.tools,
                "permissions": profile.permissions,
            },
        )

    def _run_sandbox_tests(
        self, request: CodePipelineRequest, *, phase: str
    ) -> List[SandboxResult]:
        commands = self._test_commands(request)
        return [
            self.sandbox_executor.run(
                SandboxCommand(
                    command=command,
                    cwd=request.repository_path,
                    timeout_seconds=120,
                )
            )
            for command in commands
        ]

    def _verified_findings_from_results(
        self, results: Iterable[SandboxResult]
    ) -> List[dict]:
        findings: list[dict] = []
        for result in results:
            if result.evaluation.get("passed"):
                continue
            findings.append(
                {
                    "verified": True,
                    "severity": "high"
                    if result.evaluation.get("failure_type") == "runtime_error"
                    else "medium",
                    "category": result.evaluation.get("failure_type"),
                    "summary": result.evaluation.get("summary"),
                    "command": result.command,
                    "sandbox_id": result.sandbox_id,
                    "reproduction": {
                        "exit_code": result.exit_code,
                        "stdout_tail": result.stdout[-1000:],
                        "stderr_tail": result.stderr[-1000:],
                    },
                }
            )
        return findings

    def _test_commands(self, request: CodePipelineRequest) -> List[List[str]]:
        return request.test_commands or [
            ["python", "-m", "unittest", "discover", "-s", "tests"]
        ]

    def _permissions_allow(self, profile_id: str, *, write: bool) -> bool:
        profile = self.profile_registry.get(profile_id)
        return bool(profile.permissions.get("write")) is write

    def _fix_actions(self, verified_findings: List[dict], patch_result: dict | None = None) -> List[dict]:
        if not verified_findings:
            return [{"type": "no_op", "reason": "No verified findings to fix."}]
        if patch_result and patch_result.get("applied"):
            return [
                {
                    "type": "patch_applied",
                    "changed_files": patch_result.get("changed_files", []),
                    "operation_count": len(patch_result.get("operations", [])),
                }
            ]
        return [
            {
                "type": "targeted_fix_required",
                "category": finding.get("category"),
                "summary": finding.get("summary"),
                "verification_command": finding.get("command"),
            }
            for finding in verified_findings
        ]

    def _final_status(self, stages: List[CodePipelineStage]) -> str:
        tester = stages[-1]
        if tester.status == "FAILED":
            return "FAILED"
        if any(stage.status in {"NEEDS_FIX", "FIX_APPLIED"} for stage in stages):
            return "FIXED_AND_TESTED"
        return "DONE"

    def _metadata_operations(self, request: CodePipelineRequest, key: str) -> list[dict]:
        value = request.metadata.get(key, [])
        return value if isinstance(value, list) else []

    def _fix_operations(
        self,
        request: CodePipelineRequest,
        verified_findings: List[dict],
        *,
        attempt: int,
    ) -> list[dict]:
        if not verified_findings:
            return []
        indexed_key = f"fix_patches_attempt_{attempt}"
        operations = self._metadata_operations(request, indexed_key)
        if operations:
            return operations
        operations = self._metadata_operations(request, "fix_patches")
        if operations:
            return operations
        if self.model_client is None or not request.metadata.get("use_model_patch"):
            return []
        provider_id = str(request.metadata.get("provider_id", "azure_openai"))
        model = request.metadata.get("model")
        try:
            response = self.model_client.invoke(
                ModelRequest(
                    provider_id=provider_id,
                    model=str(model) if model else None,
                    messages=[
                        ModelMessage(
                            role="system",
                            content=(
                                "Return only JSON with a patch_operations array. "
                                "Each operation must be write_file, append_file, or replace_text. "
                                "Only patch files listed in changed_files."
                            ),
                        ),
                        ModelMessage(
                            role="user",
                            content=(
                                f"Goal: {request.goal}\n"
                                f"Changed files: {request.changed_files}\n"
                                f"Verified findings: {verified_findings}\n"
                            ),
                        ),
                    ],
                    temperature=0.0,
                    max_tokens=1200,
                )
            )
        except ModelProviderError:
            return []
        return self.patch_engine.operations_from_json(response.content)
