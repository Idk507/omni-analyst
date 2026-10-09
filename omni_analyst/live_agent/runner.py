from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from omni_analyst.hooks.runtime import HookEvent, HookRuntime
from omni_analyst.live_agent.tools import (
    AgentToolRegistry,
    LiveAgentToolFactory,
    extract_action,
)
from omni_analyst.live_agent.workspace import WorkspaceManager
from omni_analyst.middleware.runtime import MiddlewareStack
from omni_analyst.model_providers.client import ModelProviderClient, ModelProviderError
from omni_analyst.models.contracts import ModelMessage, ModelRequest


SYSTEM_PROMPT = """You are OmniAgent, an autonomous engineering assistant inside an operator workspace.

You answer the user's request by emitting a SINGLE JSON action per turn. Do not output any
prose outside the JSON. The platform parses the JSON, executes the tool, and returns an
observation to you. Continue choosing tools until the task is done, then call `final_answer`.

Action grammar (one JSON object per turn):
{
  "thought": "short description of what you intend",
  "action": "<tool_name>",
  "args": { ... tool arguments ... }
}

Use `final_answer` to terminate the run with the response shown to the user. When you have
created a runnable static asset (HTML/CSS/JS), prefer to call `serve_preview` first and pass
its `preview_url` into `final_answer.args.preview_url` so the operator can render it.

Available tools:
{tool_catalog}

Workspace path: every file you create lives in a fresh sandbox folder. Paths are always
relative. Never try to read or write outside the workspace.

If you need to run code, prefer `run_python` (one self-contained snippet). Capture stdout
to communicate results to yourself before responding.

Be concise, deterministic, and stop as soon as the task is complete."""


@dataclass
class AgentTrace:
    events: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, title: str, detail: Optional[Dict[str, Any]] = None, status: str = "done") -> None:
        self.events.append(
            {
                "index": len(self.events) + 1,
                "kind": kind,
                "title": title,
                "status": status,
                "detail": detail or {},
            }
        )


@dataclass
class AgentRunResult:
    run_id: str
    workspace_id: str
    final_text: str
    preview_url: Optional[str]
    timeline: List[Dict[str, Any]]
    artifacts: List[Dict[str, Any]]
    iterations: int
    status: str = "DONE"


class LiveAgentRunner:
    """Cursor-style autonomous agent that selects tools and executes work in a sandbox."""

    def __init__(
        self,
        model_client: ModelProviderClient,
        sandbox_executor,
        workspace_manager: WorkspaceManager,
        hook_runtime: HookRuntime,
        middleware_stack: MiddlewareStack,
        ingestion_pipeline=None,
        graph_pipeline=None,
        wsil=None,
        browser_planner=None,
        max_iterations: int = 12,
    ) -> None:
        self.model_client = model_client
        self.sandbox_executor = sandbox_executor
        self.workspace_manager = workspace_manager
        self.hook_runtime = hook_runtime
        self.middleware_stack = middleware_stack
        self.ingestion_pipeline = ingestion_pipeline
        self.graph_pipeline = graph_pipeline
        self.wsil = wsil
        self.browser_planner = browser_planner
        self.max_iterations = max_iterations

    # ── Public API ──────────────────────────────────────────────────────
    def run(
        self,
        query: str,
        provider_id: str,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        max_iterations: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentRunResult:
        meta = metadata or {}
        run_id = session_id or f"agent-{int(time.time() * 1000)}"
        workspace = self.workspace_manager.create(run_id)
        workspace.write_manifest(
            {
                "run_id": run_id,
                "query": query,
                "provider_id": provider_id,
                "model": model,
                "created_at": time.time(),
            }
        )
        registry = LiveAgentToolFactory(
            workspace=workspace,
            sandbox_executor=self.sandbox_executor,
            ingestion_pipeline=self.ingestion_pipeline,
            graph_pipeline=self.graph_pipeline,
            wsil=self.wsil,
            browser_planner=self.browser_planner,
        ).build()

        trace = AgentTrace()
        trace.add("thinking", "Run accepted", {"query": query, "workspace": run_id, "provider": provider_id})

        # Lifecycle: hooks + middleware before run
        self._fire_hooks(HookEvent.USER_PROMPT_SUBMIT, {"query": query, "run_id": run_id}, trace, "UserPromptSubmit")
        before_count = len(self.middleware_stack.records())
        middleware_state = self.middleware_stack.before_agent(
            {"query": query, "session_id": run_id, "tool_name": "agent_chat", "metadata": meta}
        )
        self._add_middleware_event(trace, "Before-agent middleware", middleware_state, before_count)
        if middleware_state.get("blocked"):
            return AgentRunResult(
                run_id=run_id,
                workspace_id=run_id,
                final_text="Request blocked by policy.",
                preview_url=None,
                timeline=trace.events,
                artifacts=[],
                iterations=0,
                status="BLOCKED",
            )

        messages: List[ModelMessage] = [
            ModelMessage(
                role="system",
                content=SYSTEM_PROMPT.replace("{tool_catalog}", registry.catalog_text()),
            ),
            ModelMessage(role="user", content=query),
        ]

        final_text = ""
        preview_url: Optional[str] = None
        iterations = 0
        budget = max_iterations or self.max_iterations
        invalid_attempts = 0

        for _ in range(budget):
            iterations += 1
            response = self._invoke_model(messages, provider_id, model, trace, iterations)
            if response is None:
                final_text = "Model unavailable; aborting."
                break

            action = extract_action(response.content or "")
            if not action:
                invalid_attempts += 1
                trace.add(
                    "error",
                    f"Could not parse action (attempt {invalid_attempts})",
                    {"raw": (response.content or "")[:600]},
                    status="warn",
                )
                if invalid_attempts >= 2:
                    final_text = response.content or "Agent could not produce a structured action."
                    break
                messages.append(
                    ModelMessage(
                        role="user",
                        content="Your previous reply was not valid JSON. Respond with a single JSON action only.",
                    )
                )
                continue

            tool_name = str(action.get("action") or "").strip()
            args = action.get("args") or {}
            thought = str(action.get("thought") or "")
            if thought:
                trace.add("thinking", thought, {"iteration": iterations})

            if tool_name == "final_answer":
                final_text = str(args.get("text") or "Done.")
                preview_url = args.get("preview_url") or preview_url
                trace.add("model", "Final answer", {"text": final_text, "preview_url": preview_url})
                break

            try:
                tool = registry.get(tool_name)
            except KeyError:
                trace.add("error", f"Unknown tool: {tool_name}", {"action": action}, status="error")
                messages.append(
                    ModelMessage(
                        role="user",
                        content=f"Tool '{tool_name}' is not available. Choose from the catalog.",
                    )
                )
                continue

            self._fire_hooks(
                HookEvent.PRE_TOOL_USE,
                {"tool_name": tool_name, "args": args, "run_id": run_id},
                trace,
                f"PreToolUse: {tool_name}",
            )

            try:
                observation = tool.handler(**args) if isinstance(args, dict) else tool.handler(args)
                trace.add(
                    "tool",
                    f"Tool: {tool_name}",
                    {"args": args, "observation": _truncate_observation(observation)},
                )
            except (TypeError, ValueError, OSError, PermissionError, RuntimeError, KeyError) as exc:
                observation = {"error": str(exc), "type": type(exc).__name__}
                trace.add("error", f"Tool failed: {tool_name}", {"args": args, "error": observation}, status="error")

            self._fire_hooks(
                HookEvent.POST_TOOL_USE,
                {"tool_name": tool_name, "result": observation, "run_id": run_id},
                trace,
                f"PostToolUse: {tool_name}",
            )

            if isinstance(observation, dict):
                if observation.get("final"):
                    final_text = str(observation.get("text") or "Done.")
                    preview_url = observation.get("preview_url") or preview_url
                    trace.add("model", "Final answer", {"text": final_text, "preview_url": preview_url})
                    break
                if "preview_url" in observation and observation.get("preview_url"):
                    preview_url = observation["preview_url"]

            messages.append(ModelMessage(role="assistant", content=response.content or ""))
            messages.append(
                ModelMessage(
                    role="user",
                    content=f"Observation from {tool_name}:\n{json.dumps(_truncate_observation(observation), indent=2)}",
                )
            )

        else:
            trace.add("error", "Iteration budget exhausted", {"iterations": iterations}, status="error")

        after_count = len(self.middleware_stack.records())
        final_state = self.middleware_stack.after_agent(
            {**middleware_state, "result_summary": {"final_text": final_text[:200], "iterations": iterations}}
        )
        self._add_middleware_event(trace, "After-agent middleware", final_state, after_count)
        self._fire_hooks(HookEvent.STOP, {"run_id": run_id, "iterations": iterations}, trace, "Stop")

        snapshot = workspace.snapshot()
        return AgentRunResult(
            run_id=run_id,
            workspace_id=run_id,
            final_text=final_text or "Run completed.",
            preview_url=preview_url,
            timeline=trace.events,
            artifacts=snapshot["files"],
            iterations=iterations,
        )

    # ── Helpers ─────────────────────────────────────────────────────────
    def _invoke_model(
        self,
        messages: List[ModelMessage],
        provider_id: str,
        model: Optional[str],
        trace: AgentTrace,
        iteration: int,
    ):
        try:
            response = self.model_client.invoke(
                ModelRequest(
                    provider_id=provider_id,
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=1500,
                )
            )
            trace.add(
                "model",
                f"Model turn {iteration}",
                {"provider_id": response.provider_id, "model": response.model, "content": (response.content or "")[:1000]},
            )
            return response
        except ModelProviderError as exc:
            trace.add("error", "Model provider error", {"error": str(exc)}, status="error")
            return None

    def _fire_hooks(self, event, payload: Dict[str, Any], trace: AgentTrace, title: str) -> None:
        records = self.hook_runtime.fire_detailed(event, payload)
        if records:
            trace.add(
                "hooks",
                title,
                {"records": [record.model_dump(mode="json") for record in records]},
            )

    def _add_middleware_event(
        self,
        trace: AgentTrace,
        title: str,
        state: Dict[str, Any],
        before_count: int,
    ) -> None:
        records = self.middleware_stack.records()[before_count:]
        trace.add(
            "middleware",
            title,
            {"state": state, "records": [r.model_dump(mode="json") for r in records]},
            status="blocked" if state.get("blocked") else "done",
        )


def _truncate_observation(value: Any, *, max_chars: int = 4000) -> Any:
    """Trim large observations so the trace stays compact."""
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "…[truncated]"
    if isinstance(value, dict):
        return {k: _truncate_observation(v, max_chars=max_chars) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > 50:
            return [_truncate_observation(item, max_chars=max_chars) for item in value[:50]] + [f"…[+{len(value) - 50} more]"]
        return [_truncate_observation(item, max_chars=max_chars) for item in value]
    return value
