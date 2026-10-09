from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List

from omni_analyst.cache.semantic_cache import SemanticCache
from omni_analyst.deep_agents.backends import (
    CompositeBackend,
    LocalFilesystemBackend,
    MemoryBackend,
    StateBackend,
    VirtualFilesystemBackend,
)
from omni_analyst.deep_agents.permissions import FilesystemPermissionEvaluator
from omni_analyst.deep_agents.store import SQLiteDeepAgentStore
from omni_analyst.deep_agents.subagents import AsyncDeepSubagentRuntime, DeepSubagentRuntime
from omni_analyst.deep_agents.todos import DeepTodoStore
from omni_analyst.deep_agents.tools import DeepAgentToolRegistrar
from omni_analyst.hooks.runtime import HookRuntime
from omni_analyst.memory.fabric import MemoryFabric
from omni_analyst.middleware.runtime import MiddlewareStack, build_default_middleware_stack
from omni_analyst.model_providers.client import ModelProviderClient
from omni_analyst.models.contracts import (
    DeepAgentInterrupt,
    DeepAgentRequest,
    DeepAgentRun,
    DeepAgentStatus,
    DeepAgentStep,
    DeepAgentToolCall,
    AsyncDeepSubagentSpec,
    DeepSubagentSpec,
    ModelMessage,
    ModelRequest,
)
from omni_analyst.sandbox.executor import SandboxExecutor
from omni_analyst.skills.runtime import SkillRuntime
from omni_analyst.time_travel.checkpoint import CheckpointStore
from omni_analyst.tool_gateway.service import ToolGateway


class DeepAgentHarness:
    """LangChain Deep Agents-style harness using OmniAgent-native primitives."""

    def __init__(
        self,
        *,
        model_client: ModelProviderClient,
        tool_gateway: ToolGateway,
        memory_fabric: MemoryFabric,
        checkpoint_store: CheckpointStore,
        skill_runtime: SkillRuntime | None = None,
        middleware_stack: MiddlewareStack | None = None,
        hook_runtime: HookRuntime | None = None,
        semantic_cache: SemanticCache | None = None,
        sandbox_executor: SandboxExecutor | None = None,
        store: SQLiteDeepAgentStore | None = None,
    ) -> None:
        self.model_client = model_client
        self.tool_gateway = tool_gateway
        self.memory_fabric = memory_fabric
        self.checkpoint_store = checkpoint_store
        self.skill_runtime = skill_runtime or SkillRuntime()
        self.middleware_stack = middleware_stack or build_default_middleware_stack()
        self.hook_runtime = hook_runtime
        self.semantic_cache = semantic_cache
        self.sandbox_executor = sandbox_executor or SandboxExecutor()
        self.todo_store = DeepTodoStore()
        self.store = store
        self.async_subagent_runtime = AsyncDeepSubagentRuntime(store=store)
        self._register_default_async_subagents()
        self._runs: dict[str, DeepAgentRun] = {
            run.run_id: run for run in store.list_runs()
        } if store is not None else {}

    def run(self, request: DeepAgentRequest) -> DeepAgentRun:
        run = DeepAgentRun(request=request, status=DeepAgentStatus.RUNNING)
        backend = self._backend(request)
        subagents = self._subagents(request)
        async_subagents = self._async_subagents(request)
        registrar = DeepAgentToolRegistrar(
            gateway=self.tool_gateway,
            backend=backend,
            todo_store=self.todo_store,
            subagents=subagents,
            async_subagents=async_subagents,
            sandbox_executor=self.sandbox_executor,
            sandbox_enabled=request.sandbox_enabled,
            run_id=run.run_id,
        )
        registrar.register_all()
        try:
            messages = self._initial_messages(request)
            self.middleware_stack.before_agent(
                {"query": messages[-1].content if messages else "", "session_id": run.run_id}
            )
            for step_index in range(request.max_steps):
                step = self._run_step(run, step_index, messages)
                run.steps.append(step)
                run.todos = self.todo_store.list_todos(run.run_id)
                run.trace.append(self._trace_step(step))
                if step.interrupt:
                    run.status = DeepAgentStatus.INTERRUPTED
                    run.interrupts.append(step.interrupt)
                    break
                if step.status == DeepAgentStatus.FAILED:
                    run.status = DeepAgentStatus.FAILED
                    break
                if not step.tool_calls:
                    run.status = DeepAgentStatus.COMPLETED
                    run.final_answer = step.model_response.content if step.model_response else ""
                    break
                messages = self._messages_after_step(messages, step)
            if run.status == DeepAgentStatus.RUNNING:
                run.status = DeepAgentStatus.COMPLETED
                run.final_answer = run.steps[-1].model_response.content if run.steps and run.steps[-1].model_response else ""
            run.completed_at = self._now()
            run.checkpoint_id = self.checkpoint_store.save(
                run.model_dump(mode="json"),
                metadata={"deep_agent_run_id": run.run_id, "final": True},
            ).checkpoint_id
            run.summary = {
                "steps": len(run.steps),
                "tool_calls": sum(len(step.tool_calls) for step in run.steps),
                "todos": len(run.todos),
                "status": run.status.value,
            }
            self.memory_fabric.append_episodic(
                run.run_id,
                {"type": "deep_agent_run", "status": run.status.value, "summary": run.summary},
            )
            self.middleware_stack.after_agent({"session_id": run.run_id, "result_summary": run.summary})
            self._runs[run.run_id] = run
            self._persist_run(run)
        finally:
            registrar.unregister_all()
        return run

    def resume(self, run_id: str, decisions: List[Dict[str, Any]]) -> DeepAgentRun:
        run = self.get(run_id)
        if not run.interrupts:
            return run
        latest = run.interrupts[-1]
        latest.decisions = decisions
        latest.status = "RESOLVED"
        latest.resolved_at = self._now()
        rejected = any(decision.get("type") == "reject" for decision in decisions)
        edited = [decision for decision in decisions if decision.get("type") == "edit"]
        if rejected:
            run.status = DeepAgentStatus.FAILED
            run.final_answer = "Run stopped after human rejection."
        else:
            run.status = DeepAgentStatus.COMPLETED
            run.final_answer = (
                edited[-1].get("response")
                if edited and edited[-1].get("response")
                else "Run resumed after human decisions."
            )
        run.trace.append(
            {
                "event": "interrupt_resolved",
                "interrupt_id": latest.interrupt_id,
                "decisions": decisions,
                "status": run.status.value,
                "created_at": self._now().isoformat(),
            }
        )
        run.completed_at = self._now()
        self._runs[run_id] = run
        self._persist_run(run)
        return run

    def get(self, run_id: str) -> DeepAgentRun:
        if run_id not in self._runs and self.store is not None:
            stored = self.store.get_run(run_id)
            if stored is not None:
                self._runs[run_id] = stored
        if run_id not in self._runs:
            raise KeyError(f"Unknown deep agent run: {run_id}")
        return self._runs[run_id]

    def list_runs(self) -> List[DeepAgentRun]:
        return sorted(self._runs.values(), key=lambda run: run.created_at)

    def trace(self, run_id: str) -> List[Dict[str, Any]]:
        return self.get(run_id).trace

    def inspect_backend(self, request: DeepAgentRequest) -> Dict[str, Any]:
        backend = self._backend(request)
        return {
            "backend": request.backend,
            "workspace_root": request.workspace_root,
            "root_entries": backend.ls(".") if request.backend != "state" else [],
            "permissions": [rule.model_dump(mode="json") for rule in request.permissions],
        }

    def list_async_subagent_tasks(self) -> Dict[str, Any]:
        return self.async_subagent_runtime.list_tasks()

    def start_async_subagent_task(self, subagent: str, task: str) -> Dict[str, Any]:
        return self.async_subagent_runtime.start_task(subagent, task)

    def check_async_subagent_task(self, task_id: str) -> Dict[str, Any]:
        return self.async_subagent_runtime.check_task(task_id)

    def update_async_subagent_task(self, task_id: str, instruction: str) -> Dict[str, Any]:
        return self.async_subagent_runtime.update_task(task_id, instruction)

    def cancel_async_subagent_task(self, task_id: str) -> Dict[str, Any]:
        return self.async_subagent_runtime.cancel_task(task_id)

    def _run_step(self, run: DeepAgentRun, step_index: int, messages: List[ModelMessage]) -> DeepAgentStep:
        checkpoint = self.checkpoint_store.save(
            {"run_id": run.run_id, "step_index": step_index, "messages": [m.model_dump() for m in messages]},
            metadata={"deep_agent_run_id": run.run_id, "step_index": step_index},
        )
        step = DeepAgentStep(
            step_index=step_index,
            messages=deepcopy(messages),
            checkpoint_id=checkpoint.checkpoint_id,
            status=DeepAgentStatus.RUNNING,
        )
        try:
            self.middleware_stack.before_model({"session_id": run.run_id, "query": messages[-1].content})
            response = self.model_client.invoke(
                ModelRequest(
                    provider_id=run.request.provider_id,
                    model=run.request.model,
                    messages=messages,
                    metadata={"deep_agent_run_id": run.run_id},
                )
            )
            step.model_response = response
            tool_requests = self._parse_tool_requests(response.content)
            pending_interrupt = self._interrupt_for(run, step_index, tool_requests)
            if pending_interrupt:
                step.interrupt = pending_interrupt
                step.status = DeepAgentStatus.INTERRUPTED
                return step
            for tool_request in tool_requests:
                step.tool_calls.append(self._call_tool(run, tool_request))
            step.todos = self.todo_store.list_todos(run.run_id)
            step.status = DeepAgentStatus.COMPLETED if all(call.ok for call in step.tool_calls) else DeepAgentStatus.FAILED
            self.middleware_stack.after_model({"session_id": run.run_id, "response": response.content})
        except Exception as exc:
            step.status = DeepAgentStatus.FAILED
            step.tool_calls.append(
                DeepAgentToolCall(tool_name="model", ok=False, error=str(exc))
            )
        step.completed_at = self._now()
        return step

    def _call_tool(self, run: DeepAgentRun, tool_request: Dict[str, Any]) -> DeepAgentToolCall:
        tool_name = str(tool_request.get("tool") or tool_request.get("name"))
        arguments = dict(tool_request.get("arguments") or tool_request.get("args") or {})
        result = asyncio.run(
            self.tool_gateway.call(
                tool_name,
                arguments,
                session_id=run.run_id,
                user_id="deep-agent",
                trace_id=run.run_id,
                request_context={"deep_agent": True},
            )
        )
        return DeepAgentToolCall(
            tool_name=tool_name,
            arguments=arguments,
            ok=result.ok,
            result=result.result,
            error=result.error,
            duration_ms=result.duration_ms,
        )

    def _parse_tool_requests(self, content: str) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            calls = payload.get("tool_calls") or payload.get("tools") or []
            return calls if isinstance(calls, list) else []
        return []

    def _interrupt_for(
        self, run: DeepAgentRun, step_index: int, tool_requests: List[Dict[str, Any]]
    ) -> DeepAgentInterrupt | None:
        action_requests: list[DeepAgentToolCall] = []
        review_configs: list[dict[str, Any]] = []
        for request in tool_requests:
            name = str(request.get("tool") or request.get("name"))
            config = run.request.interrupt_on.get(name)
            if config is True or isinstance(config, dict):
                action_requests.append(
                    DeepAgentToolCall(
                        tool_name=name,
                        arguments=dict(request.get("arguments") or request.get("args") or {}),
                    )
                )
                review_configs.append(
                    {
                        "action_name": name,
                        "allowed_decisions": config.get("allowed_decisions", ["approve", "edit", "reject", "respond"])
                        if isinstance(config, dict)
                        else ["approve", "edit", "reject", "respond"],
                    }
                )
        if not action_requests:
            return None
        return DeepAgentInterrupt(
            run_id=run.run_id,
            step_index=step_index,
            action_requests=action_requests,
            review_configs=review_configs,
        )

    def _messages_after_step(self, messages: List[ModelMessage], step: DeepAgentStep) -> List[ModelMessage]:
        tool_summary = json.dumps([call.model_dump(mode="json") for call in step.tool_calls])
        return messages + [
            ModelMessage(role="assistant", content=step.model_response.content if step.model_response else ""),
            ModelMessage(role="tool", content=tool_summary),
        ]

    def _initial_messages(self, request: DeepAgentRequest) -> List[ModelMessage]:
        skill_context = ""
        if request.use_skills and request.messages:
            matches = self.skill_runtime.match(request.messages[-1].content, limit=3)
            skill_context = "\n".join(match.skill.content for match in matches)
        memory_context = "\n".join(
            str(self.memory_fabric.query(message.content, limit=3))
            for message in request.messages[-1:]
        )
        system = request.system_prompt
        system += (
            "\n\nAsync subagent rules:\n"
            "- Use deep:start_async_task for long-running or parallel work and return the full task_id to the user.\n"
            "- Do not immediately poll after launch unless the user explicitly asks for status.\n"
            "- Treat task status in conversation history as stale; call deep:check_async_task or deep:list_async_tasks before reporting it.\n"
            "- Use deep:update_async_task to steer a live task and deep:cancel_async_task to stop it."
        )
        if skill_context:
            system += f"\n\nRelevant skills:\n{skill_context}"
        if memory_context:
            system += f"\n\nRelevant memory:\n{memory_context}"
        return [ModelMessage(role="system", content=system)] + request.messages

    def _backend(self, request: DeepAgentRequest) -> VirtualFilesystemBackend:
        evaluator = FilesystemPermissionEvaluator(request.permissions)
        if request.backend == "state":
            return StateBackend(evaluator)
        local = LocalFilesystemBackend(request.workspace_root, evaluator)
        if request.backend == "composite":
            return CompositeBackend(local, routes={"/memories": MemoryBackend(self.memory_fabric)})
        return local

    def _subagents(self, request: DeepAgentRequest) -> DeepSubagentRuntime:
        runtime = DeepSubagentRuntime()
        runtime.register(
            DeepSubagentSpec(
                name="general-purpose",
                description="General isolated subagent",
                tools=["deep:read_file", "deep:grep", "deep:glob"],
            )
        )
        for spec in request.subagents:
            runtime.register(spec)
        return runtime

    def _async_subagents(self, request: DeepAgentRequest) -> AsyncDeepSubagentRuntime:
        self._register_default_async_subagents()
        for spec in request.async_subagents:
            self.async_subagent_runtime.register(spec)
        return self.async_subagent_runtime

    def _register_default_async_subagents(self) -> None:
        self.async_subagent_runtime.register(
            AsyncDeepSubagentSpec(
                name="researcher",
                description="Conducts in-depth background research and synthesis.",
                graph_id="researcher",
                tools=["deep:grep", "deep:read_file", "deep:task"],
            )
        )
        self.async_subagent_runtime.register(
            AsyncDeepSubagentSpec(
                name="coder",
                description="Runs background coding, review, and verification tasks.",
                graph_id="coder",
                tools=["deep:read_file", "deep:write_file", "deep:edit_file", "deep:execute"],
            )
        )

    def _trace_step(self, step: DeepAgentStep) -> Dict[str, Any]:
        return {
            "span_id": f"deep-step-{step.step_index}",
            "span_kind": "agent_step",
            "step_index": step.step_index,
            "status": step.status.value,
            "checkpoint_id": step.checkpoint_id,
            "tool_calls": [call.model_dump(mode="json") for call in step.tool_calls],
            "interrupt": step.interrupt.model_dump(mode="json") if step.interrupt else None,
            "started_at": step.started_at.isoformat(),
            "completed_at": step.completed_at.isoformat() if step.completed_at else None,
        }

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _persist_run(self, run: DeepAgentRun) -> None:
        if self.store is not None:
            self.store.save_run(run)
