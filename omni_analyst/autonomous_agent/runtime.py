from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from omni_analyst.autonomous_agent.config import AutonomousConfigLoader
from omni_analyst.autonomous_agent.memory import MarkdownMemoryStore
from omni_analyst.deep_agents.runtime import DeepAgentHarness
from omni_analyst.models.contracts import (
    AutonomousAgentRequest,
    AutonomousAgentRun,
    AutonomousAgentConfig,
    AutonomousApprovalPolicy,
    AutonomousSandboxMode,
    DeepAgentRequest,
    DeepAgentStatus,
    FilesystemPermissionMode,
    FilesystemPermissionRule,
    ModelMessage,
    SkillDefinition,
)
from omni_analyst.plugins.runtime import PluginRuntime
from omni_analyst.skills.creator import AutonomousSkillCreator
from omni_analyst.skills.runtime import SkillRuntime


class AutonomousAgentKernel:
    """Repo-inspired autonomous kernel on top of the Deep Agent harness."""

    def __init__(
        self,
        *,
        deep_harness: DeepAgentHarness,
        skill_runtime: SkillRuntime,
        skill_creator: AutonomousSkillCreator,
        plugin_runtime: PluginRuntime | None = None,
        config_loader: AutonomousConfigLoader | None = None,
    ) -> None:
        self.deep_harness = deep_harness
        self.skill_runtime = skill_runtime
        self.skill_creator = skill_creator
        self.plugin_runtime = plugin_runtime
        self.config_loader = config_loader or AutonomousConfigLoader()
        self._runs: dict[str, AutonomousAgentRun] = {}

    def run(self, request: AutonomousAgentRequest) -> AutonomousAgentRun:
        config = self.config_loader.load(request.config_root or request.workspace_root, profile=request.profile)
        if request.provider_id:
            config.provider_id = request.provider_id
        if request.model:
            config.model = request.model
        instructions = self.config_loader.instruction_files(
            request.workspace_root,
            max_bytes=config.project_doc_max_bytes,
        )
        markdown_memory = MarkdownMemoryStore(Path(request.workspace_root) / ".omni_memory")
        memory_context = [markdown_memory.load_core()] + markdown_memory.load_recent_daily()
        plugin_inventory = self.plugin_runtime.autonomous_inventory() if self.plugin_runtime is not None else {}
        system_prompt = self._system_prompt(request, instructions, memory_context, plugin_inventory)
        deep_request = DeepAgentRequest(
            messages=[ModelMessage(role="user", content=request.prompt)],
            provider_id=config.provider_id,
            model=config.model,
            system_prompt=system_prompt,
            workspace_root=request.workspace_root,
            max_steps=request.max_steps,
            tool_allowlist=config.allowed_tools,
            permissions=self._permissions(config),
            interrupt_on=self._interrupts(config),
            sandbox_enabled=config.sandbox_mode != AutonomousSandboxMode.READ_ONLY,
            metadata={**request.metadata, "autonomous_kernel": True, "profile": request.profile},
        )
        deep_run = self.deep_harness.run(deep_request)
        verification = self._verify(request)
        memory_updates = [
            markdown_memory.append_daily(
                "Autonomous Agent Run",
                f"Prompt: {request.prompt}\nStatus: {deep_run.status.value}\nSummary: {deep_run.summary}",
            )
        ]
        created_skill = None
        if request.learn_from_run and deep_run.status == DeepAgentStatus.COMPLETED:
            created_skill = self._create_skill_from_run(request, deep_run.final_answer, request.workspace_root)
            memory_updates.append(markdown_memory.remember(f"Created reusable skill: {created_skill.name}"))
        run = AutonomousAgentRun(
            request=request,
            config=config,
            deep_run=deep_run,
            instruction_files=instructions,
            memory_updates=memory_updates,
            created_skill=created_skill,
            verification=verification,
            status=deep_run.status,
            completed_at=datetime.now(timezone.utc),
            summary={
                "deep_run_id": deep_run.run_id,
                "instruction_files": len(instructions),
                "verification_ok": all(item["ok"] for item in verification),
                "created_skill": created_skill.name if created_skill else None,
            },
        )
        self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> AutonomousAgentRun:
        if run_id not in self._runs:
            raise KeyError(f"Unknown autonomous agent run: {run_id}")
        return self._runs[run_id]

    def list_runs(self) -> List[AutonomousAgentRun]:
        return sorted(self._runs.values(), key=lambda run: run.created_at)

    def inspect_config(self, workspace_root: str, *, profile: str = "default") -> Dict[str, Any]:
        config = self.config_loader.load(workspace_root, profile=profile)
        instructions = self.config_loader.instruction_files(workspace_root, max_bytes=config.project_doc_max_bytes)
        return {
            "config": config.model_dump(mode="json"),
            "instruction_files": instructions,
        }

    def memory_search(self, workspace_root: str, query: str, *, limit: int = 5) -> List[Dict[str, Any]]:
        return MarkdownMemoryStore(Path(workspace_root) / ".omni_memory").search(query, limit=limit)

    def _system_prompt(
        self,
        request: AutonomousAgentRequest,
        instructions: List[Dict[str, Any]],
        memory_context: List[Dict[str, Any]],
        plugin_inventory: Dict[str, Any],
    ) -> str:
        instruction_text = "\n\n".join(
            f"## {item['path']}\n{item['content']}" for item in instructions if item.get("content")
        )
        memory_text = "\n\n".join(
            f"## {item['path']}\n{item['content']}" for item in memory_context if item.get("content")
        )
        capabilities = plugin_inventory.get("capabilities", {})
        capability_text = "\n".join(
            f"- {kind}: {', '.join(item.get('name', '') for item in items[:12])}"
            for kind, items in capabilities.items()
        )
        return (
            "You are OmniAgent autonomous mode, inspired by OpenClaw, Claude Code, Codex, "
            "OpenClaude, and Hermes Agent. Operate autonomously, use tools safely, obey "
            "project instructions, checkpoint progress, verify work, and synthesize reusable skills.\n\n"
            f"## Project Instructions\n{instruction_text or 'None'}\n\n"
            f"## Persistent Markdown Memory\n{memory_text or 'None'}\n\n"
            f"## Plugin Capability Inventory\n{capability_text or 'None'}\n\n"
            f"## User Goal\n{request.prompt}"
        )

    def _permissions(self, config: AutonomousAgentConfig) -> List[FilesystemPermissionRule]:
        if config.sandbox_mode == AutonomousSandboxMode.DANGER_FULL_ACCESS:
            return []
        if config.sandbox_mode == AutonomousSandboxMode.READ_ONLY:
            return [
                FilesystemPermissionRule(
                    operations=["write"],
                    paths=["/**"],
                    mode=FilesystemPermissionMode.DENY,
                )
            ]
        rules = [
            FilesystemPermissionRule(
                operations=["read", "write"],
                paths=["/.env", "/**/.env", "/**/credentials*", "/**/*secret*"],
                mode=FilesystemPermissionMode.DENY,
            )
        ]
        for root in config.writable_roots:
            rules.append(
                FilesystemPermissionRule(
                    operations=["write"],
                    paths=[f"/{root.strip('/')}/**"],
                    mode=FilesystemPermissionMode.ALLOW,
                )
            )
        return rules

    def _interrupts(self, config: AutonomousAgentConfig) -> Dict[str, Any]:
        if config.approval_policy == AutonomousApprovalPolicy.NEVER:
            return {}
        if config.approval_policy == AutonomousApprovalPolicy.UNTRUSTED:
            return {
                "deep:write_file": True,
                "deep:edit_file": True,
                "deep:execute": True,
                "deep:task": True,
            }
        return {
            "deep:execute": {"allowed_decisions": ["approve", "reject"]},
            "deep:write_file": {"allowed_decisions": ["approve", "edit", "reject"]},
        }

    def _verify(self, request: AutonomousAgentRequest) -> List[Dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for command in request.verification_commands:
            started = time.perf_counter()
            completed = subprocess.run(
                command,
                cwd=request.workspace_root,
                text=True,
                capture_output=True,
                timeout=300,
                check=False,
            )
            results.append(
                {
                    "command": command,
                    "ok": completed.returncode == 0,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                }
            )
        return results

    def _create_skill_from_run(
        self, request: AutonomousAgentRequest, final_answer: str, workspace_root: str
    ) -> SkillDefinition:
        created = self.skill_creator.create(
            prompt=(
                f"Reusable workflow learned from autonomous run.\n"
                f"Original request: {request.prompt}\n"
                f"Outcome: {final_answer or 'completed'}"
            ),
            name=self._skill_name(request.prompt),
            root=Path(workspace_root) / ".agents" / "skills",
            reference_urls=[
                "https://github.com/openclaw/openclaw",
                "https://github.com/anthropics/claude-code",
                "https://github.com/openai/codex",
                "https://github.com/Gitlawb/openclaude",
                "https://github.com/nousresearch/hermes-agent",
            ],
            overwrite=True,
        )
        return created.skill

    def _skill_name(self, prompt: str) -> str:
        words = [word.title() for word in prompt.split()[:5]]
        return " ".join(words) or "Autonomous Learned Skill"
