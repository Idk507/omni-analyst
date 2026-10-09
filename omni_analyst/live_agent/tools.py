from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from omni_analyst.live_agent.workspace import Workspace
from omni_analyst.models.contracts import SandboxCommand
from omni_analyst.sandbox.executor import SandboxExecutor


ToolHandler = Callable[..., Dict[str, Any]]


@dataclass
class AgentTool:
    name: str
    description: str
    parameters: Dict[str, str]
    handler: ToolHandler
    category: str = "core"

    def to_descriptor(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
            "category": self.category,
        }


@dataclass
class AgentToolRegistry:
    tools: Dict[str, AgentTool] = field(default_factory=dict)

    def register(self, tool: AgentTool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        if name not in self.tools:
            raise KeyError(f"Unknown tool: {name}")
        return self.tools[name]

    def list(self) -> List[Dict[str, Any]]:
        return [tool.to_descriptor() for tool in self.tools.values()]

    def catalog_text(self) -> str:
        lines = []
        for tool in self.tools.values():
            params = ", ".join(f"{k}: {v}" for k, v in tool.parameters.items()) or "(none)"
            lines.append(f"- {tool.name} ({tool.category}) — {tool.description}\n    args: {params}")
        return "\n".join(lines)


class LiveAgentToolFactory:
    """Builds the curated tool registry an agent run uses to perform real work."""

    def __init__(
        self,
        workspace: Workspace,
        sandbox_executor: SandboxExecutor,
        ingestion_pipeline=None,
        graph_pipeline=None,
        wsil=None,
        browser_planner=None,
    ) -> None:
        self.workspace = workspace
        self.sandbox_executor = sandbox_executor
        self.ingestion_pipeline = ingestion_pipeline
        self.graph_pipeline = graph_pipeline
        self.wsil = wsil
        self.browser_planner = browser_planner

    def build(self) -> AgentToolRegistry:
        registry = AgentToolRegistry()
        registry.register(
            AgentTool(
                name="write_file",
                category="filesystem",
                description="Create or overwrite a text file in the run workspace.",
                parameters={"path": "string", "content": "string"},
                handler=self._write_file,
            )
        )
        registry.register(
            AgentTool(
                name="read_file",
                category="filesystem",
                description="Read a text file from the run workspace.",
                parameters={"path": "string"},
                handler=self._read_file,
            )
        )
        registry.register(
            AgentTool(
                name="list_files",
                category="filesystem",
                description="List files in the run workspace (optionally a sub-path).",
                parameters={"path": "string (optional, default '.')"},
                handler=self._list_files,
            )
        )
        registry.register(
            AgentTool(
                name="run_python",
                category="execution",
                description="Execute Python code inside the sandbox using the run workspace as cwd. Use stdout for results.",
                parameters={"code": "string", "timeout_seconds": "int (optional, default 30)"},
                handler=self._run_python,
            )
        )
        registry.register(
            AgentTool(
                name="run_shell",
                category="execution",
                description="Execute a shell command inside the sandbox. Args is the command split as a list.",
                parameters={"command": "list[string]", "timeout_seconds": "int (optional, default 30)"},
                handler=self._run_shell,
            )
        )
        if self.ingestion_pipeline is not None:
            registry.register(
                AgentTool(
                    name="ingest_text",
                    category="ingestion",
                    description="Ingest a text snippet through the OmniAgent ingestion pipeline.",
                    parameters={"document_id": "string", "text": "string", "media_type": "string (optional)"},
                    handler=self._ingest_text,
                )
            )
        if self.graph_pipeline is not None:
            registry.register(
                AgentTool(
                    name="build_graph",
                    category="graph",
                    description="Build or extend the LangExtract→NetworkX graph with new text.",
                    parameters={"document_id": "string", "text": "string"},
                    handler=self._build_graph,
                )
            )
            registry.register(
                AgentTool(
                    name="query_graph",
                    category="graph",
                    description="Query the current NetworkX graph for nodes and metrics.",
                    parameters={"node_type": "string (optional)", "limit": "int (optional, default 10)"},
                    handler=self._query_graph,
                )
            )
        if self.wsil is not None:
            registry.register(
                AgentTool(
                    name="web_search",
                    category="research",
                    description="Run a web search via the Web Search Intelligence Layer (DDG).",
                    parameters={"query": "string", "limit": "int (optional, default 5)"},
                    handler=self._web_search,
                )
            )
        if self.browser_planner is not None:
            registry.register(
                AgentTool(
                    name="plan_browser_actions",
                    category="browser",
                    description="Plan a Playwright automation flow for a given goal and starting URL.",
                    parameters={"goal": "string", "start_url": "string"},
                    handler=self._plan_browser_actions,
                )
            )
        registry.register(
            AgentTool(
                name="serve_preview",
                category="rendering",
                description="Mark the workspace ready to render in the inspector. Use after writing index.html for a static demo.",
                parameters={"entry": "string (optional, default 'index.html')"},
                handler=self._serve_preview,
            )
        )
        registry.register(
            AgentTool(
                name="final_answer",
                category="meta",
                description="End the run and present the final answer to the user.",
                parameters={"text": "string", "preview_url": "string (optional)"},
                handler=self._final_answer,
            )
        )
        return registry

    # ── Handlers ────────────────────────────────────────────────────────
    def _write_file(self, path: str, content: str) -> Dict[str, Any]:
        return self.workspace.write_file(path, content)

    def _read_file(self, path: str) -> Dict[str, Any]:
        return self.workspace.read_file(path)

    def _list_files(self, path: str = ".") -> Dict[str, Any]:
        return self.workspace.list_files(path)

    def _run_python(self, code: str, timeout_seconds: int = 30) -> Dict[str, Any]:
        script_name = "_omni_run.py"
        self.workspace.write_file(script_name, code)
        cmd = SandboxCommand(
            command=["python", script_name],
            cwd=str(self.workspace.path),
            timeout_seconds=timeout_seconds,
            env={},
        )
        result = self.sandbox_executor.run(cmd)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "evaluation": result.evaluation,
        }

    def _run_shell(self, command, timeout_seconds: int = 30) -> Dict[str, Any]:
        if isinstance(command, str):
            tokens = command.split()
        else:
            tokens = [str(part) for part in command]
        if not tokens:
            raise ValueError("Empty shell command")
        cmd = SandboxCommand(
            command=tokens,
            cwd=str(self.workspace.path),
            timeout_seconds=timeout_seconds,
            env={},
        )
        result = self.sandbox_executor.run(cmd)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def _ingest_text(self, document_id: str, text: str, media_type: str = "text/plain") -> Dict[str, Any]:
        return self.ingestion_pipeline.ingest(  # type: ignore[union-attr]
            document_id=document_id,
            content=text.encode("utf-8"),
            filename=f"{document_id}.txt",
            media_type=media_type,
        ).model_dump(mode="json")

    def _build_graph(self, document_id: str, text: str) -> Dict[str, Any]:
        result = self.graph_pipeline.ingest_sources(  # type: ignore[union-attr]
            [{"document_id": document_id, "text": text, "media_type": "text/plain"}],
        )
        return result

    def _query_graph(self, node_type: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
        return self.graph_pipeline.graph(node_type=node_type, limit=int(limit))  # type: ignore[union-attr]

    def _web_search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        results = self.wsil.search(query=query, limit=int(limit))  # type: ignore[union-attr]
        return {"results": results}

    def _plan_browser_actions(self, goal: str, start_url: str) -> Dict[str, Any]:
        plan = self.browser_planner.plan(goal=goal, start_url=start_url)  # type: ignore[union-attr]
        return plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan

    def _serve_preview(self, entry: str = "index.html") -> Dict[str, Any]:
        target = self.workspace.resolve(entry)
        return {
            "preview_url": f"/preview/{self.workspace.workspace_id}/{entry}",
            "entry": entry,
            "exists": target.exists(),
        }

    def _final_answer(self, text: str, preview_url: Optional[str] = None) -> Dict[str, Any]:
        return {"final": True, "text": text, "preview_url": preview_url}


# ── JSON action parsing helpers ─────────────────────────────────────────


JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_action(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first JSON action object out of a model response.

    Tries fenced ```json blocks first, then falls back to any balanced object.
    Returns None when no parseable action is found.
    """
    import json

    candidates: List[str] = []
    for match in JSON_BLOCK_PATTERN.finditer(text):
        candidates.append(match.group(1))
    if not candidates:
        candidates.extend(_iter_balanced_objects(text))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict) and "action" in payload:
            return payload
    return None


def _iter_balanced_objects(text: str) -> List[str]:
    out: List[str] = []
    depth = 0
    start = -1
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start != -1:
                out.append(text[start : index + 1])
                start = -1
    return out
