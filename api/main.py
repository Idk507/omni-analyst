from __future__ import annotations

import base64
import datetime
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from omni_analyst.audit.store import AuditStore
from omni_analyst.autonomous_agent.runtime import AutonomousAgentKernel
from omni_analyst.analytics.document_intelligence import (
    ConsensusRuntime,
    DocumentDNA,
    DriftDetector,
)
from omni_analyst.browser_automation.executor import PlaywrightAutomationExecutor
from omni_analyst.browser_automation.planner import PlaywrightAutomationPlanner
from omni_analyst.browser_automation.sessions import BrowserSessionStore
from omni_analyst.cache.semantic_cache import SemanticCache
from omni_analyst.config.env import azure_openai_env_status, load_env_file
from omni_analyst.code_workflow.pipeline import CodeWorkflowPipeline
from omni_analyst.deep_agents.runtime import DeepAgentHarness
from omni_analyst.deep_agents.store import SQLiteDeepAgentStore
from omni_analyst.explainability.panel import ConfidenceWatermarker, ExplainabilityPanelBuilder
from omni_analyst.graph.simple_builder import SimpleGraphBuilder
from omni_analyst.graph.pipeline import LangExtractNetworkXPipeline
from omni_analyst.graph.server import NetworkXGraphServer
from omni_analyst.hooks.runtime import HookRuntime
from omni_analyst.ingestion.pipeline import IngestionPipeline
from omni_analyst.ingestion.models import DocumentSource
from omni_analyst.retrieval.hybrid import HybridRetriever
from omni_analyst.hitl.store import HitlStore
from omni_analyst.management.agent_management import AgentManagementPlane
from omni_analyst.management.environment import EnvironmentMonitor
from omni_analyst.management.registry import (
    AgentRegistry,
    build_default_subagent_profiles,
)
from omni_analyst.management.session_manager import ManagedSessionStore
from omni_analyst.mcp.client import MCPStreamableHttpClient
from omni_analyst.mcp.interceptors.registry import MCPInterceptorRegistry
from omni_analyst.mcp.registry import build_default_registry
from omni_analyst.memory.fabric import MemoryFabric
from omni_analyst.model_providers.client import ModelProviderClient, ModelProviderError
from omni_analyst.model_providers.registry import build_default_model_provider_registry
from omni_analyst.models.contracts import (
    AgentRecord,
    AgentState,
    AgentManagementSnapshot,
    AuditEvent,
    AutonomousAgentRequest,
    AutonomousAgentRun,
    BrowserActionPlan,
    BrowserAutomationRequest,
    BrowserExecutionRequest,
    BrowserExecutionResult,
    BrowserSessionRecord,
    CodePipelineRequest,
    CodePipelineRun,
    DeepAgentRequest,
    DeepAgentRun,
    EnvironmentSnapshot,
    HitlDecision,
    HitlRequest,
    HookConfig,
    HookEvent,
    HookExecutionRecord,
    ManagedAgentSession,
    MiddlewareRecord,
    ModelProviderConfig,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PluginActivation,
    PluginCapability,
    PolicyDecision,
    RalphModeRequest,
    RalphModeRun,
    SemanticCacheLookup,
    SkillDefinition,
    SkillMatch,
    SubagentProfile,
    TaskContract,
    ToolRegistration,
)
from omni_analyst.orchestrator.service import OrchestratorService
from omni_analyst.orchestrator.result_bus import TaskResultBus
from omni_analyst.observability.runtime import ObservabilityRuntime, Runbook, SLODefinition
from omni_analyst.policy.engine import PolicyEngine
from omni_analyst.plugins.runtime import PluginManifest, PluginRuntime
from omni_analyst.ralph_mode.runtime import RalphModeRuntime
from omni_analyst.runtime.service import RuntimeService
from omni_analyst.sandbox.executor import SandboxExecutor
from omni_analyst.scheduler.task_scheduler import TaskScheduler
from omni_analyst.skills.creator import AutonomousSkillCreator
from omni_analyst.skills.runtime import SkillRuntime
from omni_analyst.time_travel.checkpoint import Checkpoint, CheckpointStore
from omni_analyst.tool_gateway.service import ToolGateway
from omni_analyst.wsil.search import WebSearchIntelligenceLayer
from omni_analyst.middleware.runtime import build_default_middleware_stack
from omni_analyst.connectors.presets import get_preset, list_presets
from omni_analyst.connectors.store import ConnectorStore
from omni_analyst.live_agent.runner import LiveAgentRunner
from omni_analyst.live_agent.workspace import WorkspaceManager
from omni_analyst.mcp.models import MCPServerConfig, MCPToolDescriptor
from omni_analyst.settings.store import SettingsStore


load_env_file()

app = FastAPI(title="OmniAnalyst v5 API", version="0.1.0")
ui_path = Path(__file__).resolve().parents[1] / "ui"
if ui_path.exists():
    app.mount("/operator", StaticFiles(directory=str(ui_path), html=True), name="operator")

registry = AgentRegistry()
subagent_profiles = build_default_subagent_profiles()
managed_sessions = ManagedSessionStore()
environment_monitor = EnvironmentMonitor()
browser_sessions = BrowserSessionStore(os.getenv("OMNI_BROWSER_SESSIONS_PATH", ".browser-artifacts/sessions.json"))
sandbox_executor = SandboxExecutor()
checkpoint_store = CheckpointStore()
deep_agent_store = SQLiteDeepAgentStore()
ralph_mode = RalphModeRuntime(checkpoint_store=checkpoint_store)
hook_runtime = HookRuntime()
semantic_cache = SemanticCache(
    storage_path=os.getenv("OMNI_SEMANTIC_CACHE_PATH", ".omni_memory/semantic_cache.json"),
    backend=os.getenv("OMNI_SEMANTIC_CACHE_BACKEND", "json"),
    redis_url=os.getenv("OMNI_REDIS_URL"),
)
skill_runtime = SkillRuntime()
skill_creator = AutonomousSkillCreator(skill_runtime=skill_runtime)
middleware_stack = build_default_middleware_stack()
explainability_builder = ExplainabilityPanelBuilder()
confidence_watermarker = ConfidenceWatermarker()
document_dna = DocumentDNA()
drift_detector = DriftDetector()
consensus_runtime = ConsensusRuntime()
wsil = WebSearchIntelligenceLayer(
    semantic_cache=semantic_cache,
    hook_runtime=hook_runtime,
)
model_provider_registry = build_default_model_provider_registry()
model_provider_client = ModelProviderClient(model_provider_registry)
settings_store = SettingsStore()
connector_store = ConnectorStore()


def _sync_connectors_into_mcp_registry() -> None:
    for config in connector_store.to_mcp_server_configs():
        mcp_registry.register_server(
            MCPServerConfig(
                name=config["name"],
                url=config["url"],
                transport=config.get("transport", "streamable_http"),
                interceptor_names=config.get("interceptor_names", []),
                tools=[
                    MCPToolDescriptor(
                        name=str(tool.get("name", "")),
                        description=str(tool.get("description", "")),
                        input_schema=dict(tool.get("input_schema", {})),
                    )
                    for tool in config.get("tools", [])
                    if tool.get("name")
                ],
            )
        )
scheduler = TaskScheduler()
policy_engine = PolicyEngine()
audit_store = AuditStore()
hitl_store = HitlStore()
memory_fabric = MemoryFabric()
mcp_registry = build_default_registry()
mcp_interceptor_registry = MCPInterceptorRegistry(
    audit_store=audit_store,
    base_state={"runtime": "api"},
)
_sync_connectors_into_mcp_registry()
mcp_client = MCPStreamableHttpClient(
    registry=mcp_registry,
    interceptor_registry=mcp_interceptor_registry,
)
ingestion_pipeline = IngestionPipeline()

# ─── In-memory document store & hybrid retriever for RAG ─────────────────────
_document_store: Dict[str, Dict[str, Any]] = {}   # doc_id -> doc info + raw chunks
_document_lock = threading.Lock()
_hybrid_retriever = HybridRetriever()
_ALLOWED_DOC_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".csv", ".json", ".docx", ".doc",
    ".xlsx", ".xls", ".pptx", ".ppt",
}
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def _rebuild_rag_index() -> None:
    """Rebuild the HybridRetriever index from all stored document chunks."""
    all_chunks = []
    for doc in _document_store.values():
        all_chunks.extend(doc.get("chunks_raw", []))
    _hybrid_retriever.index(all_chunks)
graph_builder = SimpleGraphBuilder()
graph_pipeline = LangExtractNetworkXPipeline(ingestion_pipeline=ingestion_pipeline, graph_builder=graph_builder)
networkx_graph_server = NetworkXGraphServer(graph_pipeline)
tool_gateway = ToolGateway(mcp_client=mcp_client, hook_runtime=hook_runtime)


def _observability_alert_hook(alert) -> None:
    hook_runtime.fire_detailed(
        "Notification",
        {"event": "observability_alert", "alert": alert.__dict__},
    )


observability = ObservabilityRuntime(alert_hook=_observability_alert_hook)
plugin_runtime = PluginRuntime(
    hook_runtime=hook_runtime,
    skill_runtime=skill_runtime,
    tool_gateway=tool_gateway,
)


def _register_networkx_graph_tools() -> None:
    for tool in networkx_graph_server.tools():
        name = f"mcp:{tool['name']}"
        handler = getattr(networkx_graph_server, tool["name"].replace("networkx_graph_", ""))
        tool_gateway.register_local_tool(
            name,
            handler,
            ToolRegistration(
                tool_name=name,
                source="mcp",
                namespace="networkx_graph",
                description=tool["description"],
                input_schema=tool["input_schema"],
                timeout_seconds=60,
            ),
        )


_register_networkx_graph_tools()
deep_agent_harness = DeepAgentHarness(
    model_client=model_provider_client,
    tool_gateway=tool_gateway,
    memory_fabric=memory_fabric,
    checkpoint_store=checkpoint_store,
    skill_runtime=skill_runtime,
    middleware_stack=middleware_stack,
    hook_runtime=hook_runtime,
    semantic_cache=semantic_cache,
    sandbox_executor=sandbox_executor,
    store=deep_agent_store,
)
autonomous_agent = AutonomousAgentKernel(
    deep_harness=deep_agent_harness,
    skill_runtime=skill_runtime,
    skill_creator=skill_creator,
    plugin_runtime=plugin_runtime,
)
code_workflow = CodeWorkflowPipeline(
    profile_registry=subagent_profiles,
    sandbox_executor=sandbox_executor,
    model_client=model_provider_client,
)
browser_planner = PlaywrightAutomationPlanner()
browser_executor = PlaywrightAutomationExecutor()
workspace_manager = WorkspaceManager()
live_agent_runner = LiveAgentRunner(
    model_client=model_provider_client,
    sandbox_executor=sandbox_executor,
    workspace_manager=workspace_manager,
    hook_runtime=hook_runtime,
    middleware_stack=middleware_stack,
    ingestion_pipeline=ingestion_pipeline,
    graph_pipeline=graph_pipeline,
    wsil=wsil,
    browser_planner=browser_planner,
)
agent_management = AgentManagementPlane(
    agent_registry=registry,
    profile_registry=subagent_profiles,
    session_store=managed_sessions,
    environment_monitor=environment_monitor,
)
orchestrator = OrchestratorService(
    scheduler=scheduler,
    policy_engine=policy_engine,
    audit_store=audit_store,
    hitl_store=hitl_store,
    result_bus=TaskResultBus(),
)
runtime = RuntimeService(
    orchestrator=orchestrator,
    memory_fabric=memory_fabric,
    mcp_client=mcp_client,
    ingestion_pipeline=ingestion_pipeline,
    graph_builder=graph_builder,
    tool_gateway=tool_gateway,
)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OperatorRunRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = None
    provider_id: str | None = None
    model: str | None = None
    include_model: bool = True
    include_graph_tool: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConnectorUpsertRequest(BaseModel):
    id: str = Field(min_length=1)
    name: str | None = None
    category: str = "custom"
    description: str = ""
    url: str
    transport: str = "streamable_http"
    enabled: bool = True
    auth_type: str = "none"
    auth_env_var: str | None = None
    tools: List[Dict[str, Any]] = Field(default_factory=list)


class ConnectorPresetActivateRequest(BaseModel):
    preset_id: str = Field(min_length=1)
    url: str | None = None
    auth_env_var: str | None = None
    enabled: bool = True


class SettingsUpdateRequest(BaseModel):
    active_provider_id: str | None = None
    active_model: str | None = None
    theme: str | None = None
    default_mode: str | None = None
    telemetry_opt_in: bool | None = None


class OllamaDiscoveryRequest(BaseModel):
    base_url: str = "http://localhost:11434"


class AgentChatRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = None
    provider_id: str | None = None
    model: str | None = None
    max_iterations: int | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RegisterAgentRequest(BaseModel):
    agent: AgentRecord


class RegisterModelProviderRequest(BaseModel):
    provider: ModelProviderConfig


class AsyncSubagentUpdateRequest(BaseModel):
    instruction: str = Field(min_length=1)


class AsyncSubagentStartRequest(BaseModel):
    subagent: str = Field(min_length=1)
    task: str = Field(min_length=1)


class HookRegisterRequest(BaseModel):
    hook: HookConfig


class HookFireRequest(BaseModel):
    event: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class PluginActivateRequest(BaseModel):
    plugin_id: str


class PluginInstallRequest(BaseModel):
    package: str
    expected_sha256: str | None = None
    overwrite: bool = False


class PluginSignRequest(BaseModel):
    manifest_path: str
    secret: str | None = None


class MarketplaceLoadRequest(BaseModel):
    index_path: str


class SkillMatchRequest(BaseModel):
    text: str = Field(min_length=1)
    limit: int = 5


class SkillDiscoverRequest(BaseModel):
    root: str = "."
    namespace: str = "local"


class SkillCreateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    name: str | None = None
    description: str | None = None
    root: str = ".agents/skills"
    namespace: str = "local"
    reference_urls: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    include_python_runner: bool = True
    fetch_references: bool = False
    overwrite: bool = False


class SemanticCachePutRequest(BaseModel):
    key: str
    query: str
    value: Dict[str, Any] = Field(default_factory=dict)
    namespace: str = "default"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int | None = None


class SemanticCacheLookupRequest(BaseModel):
    query: str
    namespace: str = "default"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticCacheInvalidateRequest(BaseModel):
    namespace: str | None = None
    key: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CreateManagedSessionRequest(BaseModel):
    session_id: str = Field(min_length=1)
    user_goal: str = Field(min_length=1)
    active_agent_id: str | None = None
    subagent_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AssignSubagentsRequest(BaseModel):
    profile_ids: List[str] = Field(min_length=1)


class EnvironmentSnapshotRequest(BaseModel):
    session_id: str | None = None
    cwd: str | None = None
    required_tools: List[str] = Field(default_factory=lambda: ["python", "git"])
    sandbox_active: bool = False
    browser_active: bool = False


class IngestionDiagnosticsRequest(BaseModel):
    source: Dict[str, Any]


class GraphPipelineRequest(BaseModel):
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    depth: str = "deep"
    use_provider: bool = False


class GraphQueryRequest(BaseModel):
    version_id: str | None = None
    node_type: str | None = None
    label_contains: str | None = None
    limit: int = 50


class SLIRecordRequest(BaseModel):
    name: str = Field(min_length=1)
    value: float
    labels: Dict[str, Any] = Field(default_factory=dict)


class SLODefinitionRequest(BaseModel):
    name: str = Field(min_length=1)
    sli: str = Field(min_length=1)
    target: float
    window_seconds: int = 3600
    comparator: str = ">="
    runbook_id: str | None = None


class RunbookRegisterRequest(BaseModel):
    runbook_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    steps: List[str] = Field(default_factory=list)
    automation: str | None = None


class WorkerLeaseRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    limit: int = 1
    lease_seconds: int = 300


class MemoryMergeRequest(BaseModel):
    item: Dict[str, Any] = Field(default_factory=dict)


class MemoryQueryRequest(BaseModel):
    text: str = Field(min_length=1)
    layers: List[str] | None = None
    owner_id: str | None = None
    limit: int = 10


class MemoryCompactRequest(BaseModel):
    keep_last: int = 20


class MemoryImportRequest(BaseModel):
    state: Dict[str, Any] = Field(default_factory=dict)
    merge: bool = True


class ProjectMemoryLoadRequest(BaseModel):
    root: str = "."


class PluginDiscoverRequest(BaseModel):
    root: str = "."


class CheckpointSaveRequest(BaseModel):
    state: Dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CheckpointBranchRequest(BaseModel):
    updates: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExplainabilityRequest(BaseModel):
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    retrieval: List[Dict[str, Any]] = Field(default_factory=list)
    graph_paths: List[Dict[str, Any]] = Field(default_factory=list)
    middleware: List[str] = Field(default_factory=list)
    hook_records: List[Dict[str, Any]] = Field(default_factory=list)
    cache_records: List[Dict[str, Any]] = Field(default_factory=list)
    confidence_marks: List[Dict[str, Any]] = Field(default_factory=list)


class ConfidenceWatermarkRequest(BaseModel):
    sentences: List[str] = Field(default_factory=list)
    evidence_scores: List[float] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)


class DocumentFingerprintRequest(BaseModel):
    text: str = Field(min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DriftCompareRequest(BaseModel):
    previous: Dict[str, Any] = Field(default_factory=dict)
    current: Dict[str, Any] = Field(default_factory=dict)
    review_threshold: float = 0.35


class ConsensusRequest(BaseModel):
    votes: List[Dict[str, Any]] = Field(default_factory=list)
    quorum: float = 0.67
    weight_key: str = "confidence"


class SubmitTaskRequest(BaseModel):
    task: TaskContract


class UpdatePermissionsRequest(BaseModel):
    permissions: Dict[str, Any]


class HitlResolutionRequest(BaseModel):
    hitl_id: UUID
    reviewer: str = Field(min_length=1)
    note: str | None = None


class UploadedFilePayload(BaseModel):
    document_id: str | None = None
    filename: str = Field(min_length=1)
    media_type: str | None = None
    path: str | None = None
    content_base64: str | None = None
    text: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PolicyValidationRequest(BaseModel):
    task: TaskContract


class UploadAndQueryRequest(QueryRequest):
    document_ids: List[str] = Field(default_factory=list)
    uploaded_files: List[UploadedFilePayload] = Field(default_factory=list)


class RAGQueryRequest(BaseModel):
    query: str = Field(min_length=1)
    document_ids: List[str] = Field(default_factory=list)
    session_id: str | None = None
    top_k: int = 5
    metadata: Dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v5/query")
def query_endpoint(payload: QueryRequest) -> Dict[str, Any]:
    middleware_state = middleware_stack.before_agent(
        {
            "query": payload.query,
            "session_id": payload.session_id,
            "metadata": payload.metadata,
        }
    )
    if middleware_state.get("blocked"):
        raise HTTPException(status_code=400, detail=middleware_state.get("warnings", []))
    result = runtime.submit_query(
        query=payload.query,
        session_id=payload.session_id,
        metadata={**payload.metadata, "middleware": middleware_state},
    )
    final_state = middleware_stack.after_agent(
        {**middleware_state, "result_summary": {"accepted": result.get("accepted")}}
    )
    result["middleware_records"] = [
        record.model_dump(mode="json") for record in middleware_stack.records()
    ]
    result["middleware_state"] = final_state
    return result


@app.post("/api/v5/agent/chat")
def agent_chat(payload: AgentChatRequest) -> Dict[str, Any]:
    provider_id = payload.provider_id or settings_store.active_provider_id()
    model = payload.model or settings_store.active_model()
    result = live_agent_runner.run(
        query=payload.query,
        provider_id=provider_id,
        model=model,
        session_id=payload.session_id,
        max_iterations=payload.max_iterations,
        metadata=payload.metadata,
    )
    return {
        "run_id": result.run_id,
        "workspace_id": result.workspace_id,
        "status": result.status,
        "iterations": result.iterations,
        "answer": result.final_text,
        "preview_url": result.preview_url,
        "timeline": result.timeline,
        "artifacts": result.artifacts,
    }


@app.get("/api/v5/agent/workspaces")
def list_agent_workspaces() -> Dict[str, Any]:
    return {"workspaces": workspace_manager.list()}


@app.get("/api/v5/agent/workspaces/{workspace_id}")
def get_agent_workspace(workspace_id: str) -> Dict[str, Any]:
    try:
        return workspace_manager.get(workspace_id).snapshot()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/preview/{workspace_id}/{file_path:path}")
def serve_workspace_preview(workspace_id: str, file_path: str) -> Any:
    from fastapi.responses import FileResponse

    try:
        workspace = workspace_manager.get(workspace_id)
        target = workspace.resolve(file_path or "index.html")
    except (FileNotFoundError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found in workspace")
    return FileResponse(target)


@app.get("/preview/{workspace_id}")
def serve_workspace_preview_index(workspace_id: str) -> Any:
    return serve_workspace_preview(workspace_id, "index.html")


@app.post("/api/v5/operator/runs")
def operator_run(payload: OperatorRunRequest) -> Dict[str, Any]:
    session_id = payload.session_id or f"operator-{uuid4()}"
    trace_id = f"trace-{uuid4()}"
    timeline: list[dict[str, Any]] = []
    provider_id = payload.provider_id or settings_store.active_provider_id()
    model = payload.model or settings_store.active_model()

    def add_event(kind: str, title: str, detail: Dict[str, Any] | None = None, status: str = "done") -> None:
        timeline.append(
            {
                "index": len(timeline) + 1,
                "kind": kind,
                "title": title,
                "status": status,
                "detail": detail or {},
            }
        )

    add_event(
        "thinking",
        "Understanding request",
        {
            "summary": "Classify the user goal, prepare middleware context, then decide which runtime surfaces to use.",
            "query": payload.query,
        },
        status="active",
    )

    prompt_hooks = hook_runtime.fire_detailed(
        HookEvent.USER_PROMPT_SUBMIT,
        {"query": payload.query, "session_id": session_id, "trace_id": trace_id},
    )
    add_event(
        "hooks",
        "UserPromptSubmit hooks",
        {"records": [record.model_dump(mode="json") for record in prompt_hooks]},
    )

    before_record_count = len(middleware_stack.records())
    middleware_state = middleware_stack.before_agent(
        {
            "query": payload.query,
            "session_id": session_id,
            "trace_id": trace_id,
            "metadata": payload.metadata,
            "tool_name": "operator_run",
        }
    )
    middleware_records = middleware_stack.records()[before_record_count:]
    add_event(
        "middleware",
        "Before-agent middleware",
        {
            "state": middleware_state,
            "records": [record.model_dump(mode="json") for record in middleware_records],
        },
        status="blocked" if middleware_state.get("blocked") else "done",
    )
    if middleware_state.get("blocked"):
        return {
            "run_id": session_id,
            "trace_id": trace_id,
            "status": "BLOCKED",
            "answer": "",
            "timeline": timeline,
        }

    add_event(
        "thinking",
        "Planning runtime work",
        {"plan": ["submit query to scheduler", "collect graph context", "optionally call model", "publish trace"]},
    )
    runtime_result = runtime.submit_query(
        query=payload.query,
        session_id=session_id,
        metadata={**payload.metadata, "operator_trace": trace_id},
    )
    add_event("planner", "Task planner and scheduler", runtime_result)

    tool_events: list[dict[str, Any]] = []
    if payload.include_graph_tool:
        graph_tool = tool_gateway.call(
            "mcp:networkx_graph_query",
            {"node_type": "entity", "limit": 8},
            session_id=session_id,
            trace_id=trace_id,
            request_context={"operator_run": True},
        )
        import asyncio

        graph_tool_result = asyncio.run(graph_tool)
        tool_events.append(
            {
                "tool_name": graph_tool_result.tool_name,
                "provider": graph_tool_result.provider,
                "ok": graph_tool_result.ok,
                "duration_ms": graph_tool_result.duration_ms,
                "result": graph_tool_result.result,
                "error": graph_tool_result.error,
                "hook_records": [record.model_dump(mode="json") for record in graph_tool_result.hook_records],
            }
        )
    add_event("tools", "Tool calls", {"calls": tool_events})

    model_response = None
    if payload.include_model:
        model_hooks = hook_runtime.fire_detailed(
            HookEvent.PRE_MODEL_USE,
            {"provider_id": provider_id, "model": model, "session_id": session_id},
        )
        add_event(
            "hooks",
            "PreModelUse hooks",
            {"records": [record.model_dump(mode="json") for record in model_hooks]},
        )
        before_model_record_count = len(middleware_stack.records())
        model_state = middleware_stack.before_model(
            {
                "query": payload.query,
                "provider_id": provider_id,
                "model": model,
                "session_id": session_id,
            }
        )
        add_event(
            "middleware",
            "Before-model middleware",
            {
                "state": model_state,
                "records": [
                    record.model_dump(mode="json")
                    for record in middleware_stack.records()[before_model_record_count:]
                ],
            },
        )
        model_response = model_provider_client.invoke(
            ModelRequest(
                provider_id=provider_id,
                model=model,
                messages=[
                    ModelMessage(
                        role="system",
                        content=(
                            "You are OmniAnalyst operator mode. Answer concisely and mention which "
                            "runtime surfaces were used when relevant."
                        ),
                    ),
                    ModelMessage(role="user", content=payload.query),
                ],
                temperature=0.2,
                max_tokens=700,
            )
        )
        add_event(
            "model",
            "Model response",
            {
                "provider_id": model_response.provider_id,
                "model": model_response.model,
                "content": model_response.content,
                "usage": model_response.usage,
            },
        )
        post_model_hooks = hook_runtime.fire_detailed(
            HookEvent.POST_MODEL_USE,
            {"provider_id": provider_id, "model": model_response.model, "session_id": session_id},
        )
        add_event(
            "hooks",
            "PostModelUse hooks",
            {"records": [record.model_dump(mode="json") for record in post_model_hooks]},
        )

    before_after_count = len(middleware_stack.records())
    final_state = middleware_stack.after_agent(
        {
            **middleware_state,
            "result_summary": {
                "accepted": runtime_result.get("accepted"),
                "tool_calls": len(tool_events),
                "model_used": bool(model_response),
            },
        }
    )
    add_event(
        "middleware",
        "After-agent middleware",
        {
            "state": final_state,
            "records": [
                record.model_dump(mode="json")
                for record in middleware_stack.records()[before_after_count:]
            ],
        },
    )
    stop_hooks = hook_runtime.fire_detailed(
        HookEvent.STOP,
        {"session_id": session_id, "trace_id": trace_id, "status": "DONE"},
    )
    add_event(
        "hooks",
        "Stop hooks",
        {"records": [record.model_dump(mode="json") for record in stop_hooks]},
    )
    return {
        "run_id": session_id,
        "trace_id": trace_id,
        "status": "DONE",
        "answer": model_response.content if model_response else "Query accepted and runtime trace completed.",
        "timeline": timeline,
        "runtime_result": runtime_result,
        "middleware_records": [record.model_dump(mode="json") for record in middleware_stack.records()],
        "hook_records": [record.model_dump(mode="json") for record in hook_runtime.records()],
    }


@app.post("/api/v5/upload-and-query")
def upload_and_query_endpoint(payload: UploadAndQueryRequest) -> Dict[str, Any]:
    metadata = dict(payload.metadata)
    metadata["has_document"] = True
    metadata["document_ids"] = payload.document_ids
    metadata["uploaded_files"] = [
        uploaded.model_dump(exclude_none=True) for uploaded in payload.uploaded_files
    ]
    return runtime.submit_query(
        query=payload.query,
        session_id=payload.session_id,
        metadata=metadata,
    )


@app.get("/api/v5/agents")
def list_agents() -> List[AgentRecord]:
    return registry.list_agents()


@app.post("/api/v5/agents/register")
def register_agent(payload: RegisterAgentRequest) -> AgentRecord:
    return registry.register(payload.agent)


@app.get("/api/v5/model-providers")
def list_model_providers() -> List[ModelProviderConfig]:
    return model_provider_registry.list()


@app.get("/api/v5/model-providers/azure-openai/env-status")
def azure_openai_status() -> Dict[str, Any]:
    return azure_openai_env_status()


@app.post("/api/v5/model-providers/register")
def register_model_provider(payload: RegisterModelProviderRequest) -> ModelProviderConfig:
    return model_provider_registry.register(payload.provider)


@app.post("/api/v5/model-providers/discover-ollama")
def discover_ollama_models(payload: OllamaDiscoveryRequest) -> Dict[str, Any]:
    base_url = payload.base_url.rstrip("/")
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{base_url}/api/tags")
        if response.status_code != 200:
            return {"available": False, "models": [], "base_url": base_url, "status_code": response.status_code}
        data = response.json()
        models = [model.get("name") for model in data.get("models", []) if model.get("name")]
        return {"available": True, "models": models, "base_url": base_url}
    except (httpx.HTTPError, ValueError):
        return {"available": False, "models": [], "base_url": base_url}


@app.get("/api/v5/settings")
def get_settings() -> Dict[str, Any]:
    return settings_store.get()


@app.post("/api/v5/settings")
def update_settings(payload: SettingsUpdateRequest) -> Dict[str, Any]:
    update = {key: value for key, value in payload.model_dump().items() if value is not None}
    return settings_store.update(update)


@app.post("/api/v5/settings/reset")
def reset_settings() -> Dict[str, Any]:
    return settings_store.reset()


@app.get("/api/v5/connectors")
def list_connectors() -> List[Dict[str, Any]]:
    return connector_store.list()


@app.get("/api/v5/connectors/presets")
def list_connector_presets() -> List[Dict[str, Any]]:
    return list_presets()


@app.post("/api/v5/connectors")
def upsert_connector(payload: ConnectorUpsertRequest) -> Dict[str, Any]:
    record = connector_store.upsert(
        {
            "id": payload.id,
            "name": payload.name or payload.id,
            "category": payload.category,
            "description": payload.description,
            "url": payload.url,
            "transport": payload.transport,
            "enabled": payload.enabled,
            "auth": {"type": payload.auth_type, "env_var": payload.auth_env_var},
            "tools": payload.tools,
        }
    )
    _sync_connectors_into_mcp_registry()
    return record


@app.post("/api/v5/connectors/preset/activate")
def activate_connector_preset(payload: ConnectorPresetActivateRequest) -> Dict[str, Any]:
    try:
        preset = get_preset(payload.preset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    auth = dict(preset.get("auth") or {})
    if payload.auth_env_var:
        auth["env_var"] = payload.auth_env_var
    record = connector_store.upsert(
        {
            "id": preset["id"],
            "name": preset["name"],
            "category": preset.get("category", "custom"),
            "description": preset.get("description", ""),
            "url": payload.url or preset.get("default_url", ""),
            "transport": preset.get("transport", "streamable_http"),
            "enabled": payload.enabled,
            "auth": auth,
            "tools": preset.get("tools", []),
        }
    )
    _sync_connectors_into_mcp_registry()
    return record


@app.delete("/api/v5/connectors/{connector_id}")
def remove_connector(connector_id: str) -> Dict[str, Any]:
    removed = connector_store.remove(connector_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Connector not found")
    return {"removed": True, "connector_id": connector_id}


@app.post("/api/v5/model-providers/invoke")
def invoke_model_provider(payload: ModelRequest) -> ModelResponse:
    try:
        cache_key = f"model:{payload.provider_id}:{payload.model or ''}:{payload.messages[-1].content if payload.messages else ''}"
        cached = semantic_cache.get(
            cache_key,
            namespace="model",
            metadata={"provider": payload.provider_id, "model": payload.model or ""},
        )
        if cached and "response" in cached:
            return ModelResponse(**cached["response"])
        middleware_stack.before_model(
            {
                "provider_id": payload.provider_id,
                "model": payload.model,
                "query": payload.messages[-1].content if payload.messages else "",
            }
        )
        hook_runtime.fire_detailed(
            "PreModelUse",
            {"provider_id": payload.provider_id, "model": payload.model},
        )
        response = model_provider_client.invoke(payload)
        hook_runtime.fire_detailed(
            "PostModelUse",
            {"provider_id": payload.provider_id, "model": response.model, "response": response.content},
        )
        middleware_stack.after_model({"response": response.content})
        semantic_cache.put(
            cache_key,
            cache_key,
            {"response": response.model_dump(mode="json")},
            namespace="model",
            metadata={"provider": payload.provider_id, "model": response.model},
            ttl_seconds=3600,
        )
        return response
    except (KeyError, ModelProviderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v5/agents/bootstrap-defaults")
def bootstrap_default_agents() -> List[AgentRecord]:
    return agent_management.bootstrap_default_agents()


@app.get("/api/v5/management/snapshot")
def management_snapshot(session_id: str | None = None) -> AgentManagementSnapshot:
    return agent_management.snapshot(session_id=session_id)


@app.get("/api/v5/subagents/profiles")
def list_subagent_profiles() -> List[SubagentProfile]:
    return subagent_profiles.list_profiles()


@app.post("/api/v5/sessions/managed")
def create_managed_session(
    payload: CreateManagedSessionRequest,
) -> ManagedAgentSession:
    return managed_sessions.create(
        ManagedAgentSession(
            session_id=payload.session_id,
            user_goal=payload.user_goal,
            active_agent_id=payload.active_agent_id,
            subagent_ids=payload.subagent_ids,
            metadata=payload.metadata,
        )
    )


@app.get("/api/v5/sessions/managed")
def list_managed_sessions() -> List[ManagedAgentSession]:
    return managed_sessions.list_sessions()


@app.get("/api/v5/sessions/managed/{session_id}")
def get_managed_session(session_id: str) -> ManagedAgentSession:
    try:
        return managed_sessions.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/sessions/managed/{session_id}/subagents")
def assign_session_subagents(
    session_id: str,
    payload: AssignSubagentsRequest,
) -> ManagedAgentSession:
    try:
        return agent_management.assign_subagents(
            session_id=session_id,
            profile_ids=payload.profile_ids,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/environment/snapshot")
def snapshot_environment(payload: EnvironmentSnapshotRequest) -> EnvironmentSnapshot:
    return environment_monitor.snapshot(
        session_id=payload.session_id,
        cwd=payload.cwd,
        required_tools=payload.required_tools,
        sandbox_active=payload.sandbox_active,
        browser_active=payload.browser_active,
    )


@app.post("/api/v5/code/pipeline")
def run_code_pipeline(payload: CodePipelineRequest) -> CodePipelineRun:
    return code_workflow.run(payload)


@app.post("/api/v5/ralph-mode/run")
def run_ralph_mode(payload: RalphModeRequest) -> RalphModeRun:
    try:
        return ralph_mode.run(payload)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v5/ralph-mode/runs")
def list_ralph_runs() -> List[RalphModeRun]:
    return ralph_mode.list_runs()


@app.get("/api/v5/ralph-mode/runs/{run_id}")
def get_ralph_run(run_id: str) -> RalphModeRun:
    try:
        return ralph_mode.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/deep-agents/run")
def run_deep_agent(payload: DeepAgentRequest) -> DeepAgentRun:
    try:
        return deep_agent_harness.run(payload)
    except (OSError, ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v5/deep-agents/runs")
def list_deep_agent_runs() -> List[DeepAgentRun]:
    return deep_agent_harness.list_runs()


@app.get("/api/v5/deep-agents/runs/{run_id}")
def get_deep_agent_run(run_id: str) -> DeepAgentRun:
    try:
        return deep_agent_harness.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/deep-agents/runs/{run_id}/resume")
def resume_deep_agent_run(run_id: str, decisions: List[Dict[str, Any]]) -> DeepAgentRun:
    try:
        return deep_agent_harness.resume(run_id, decisions)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v5/deep-agents/runs/{run_id}/trace")
def deep_agent_trace(run_id: str) -> List[Dict[str, Any]]:
    try:
        return deep_agent_harness.trace(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/deep-agents/backends/inspect")
def inspect_deep_agent_backend(payload: DeepAgentRequest) -> Dict[str, Any]:
    try:
        return deep_agent_harness.inspect_backend(payload)
    except (OSError, ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v5/deep-agents/async-subagents/tasks")
def list_async_subagent_tasks() -> Dict[str, Any]:
    return deep_agent_harness.list_async_subagent_tasks()


@app.post("/api/v5/deep-agents/async-subagents/tasks")
def start_async_subagent_task(payload: AsyncSubagentStartRequest) -> Dict[str, Any]:
    try:
        return deep_agent_harness.start_async_subagent_task(payload.subagent, payload.task)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v5/deep-agents/async-subagents/tasks/{task_id}")
def check_async_subagent_task(task_id: str) -> Dict[str, Any]:
    try:
        return deep_agent_harness.check_async_subagent_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/deep-agents/async-subagents/tasks/{task_id}/update")
def update_async_subagent_task(task_id: str, payload: AsyncSubagentUpdateRequest) -> Dict[str, Any]:
    try:
        return deep_agent_harness.update_async_subagent_task(task_id, payload.instruction)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v5/deep-agents/async-subagents/tasks/{task_id}/cancel")
def cancel_async_subagent_task(task_id: str) -> Dict[str, Any]:
    try:
        return deep_agent_harness.cancel_async_subagent_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/autonomous-agent/run")
def run_autonomous_agent(payload: AutonomousAgentRequest) -> AutonomousAgentRun:
    try:
        return autonomous_agent.run(payload)
    except (OSError, ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v5/autonomous-agent/runs")
def list_autonomous_agent_runs() -> List[AutonomousAgentRun]:
    return autonomous_agent.list_runs()


@app.get("/api/v5/autonomous-agent/runs/{run_id}")
def get_autonomous_agent_run(run_id: str) -> AutonomousAgentRun:
    try:
        return autonomous_agent.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v5/autonomous-agent/config/inspect")
def inspect_autonomous_agent_config(workspace_root: str = ".", profile: str = "default") -> Dict[str, Any]:
    try:
        return autonomous_agent.inspect_config(workspace_root, profile=profile)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v5/autonomous-agent/memory/search")
def search_autonomous_agent_memory(
    workspace_root: str = ".", query: str = "", limit: int = 5
) -> List[Dict[str, Any]]:
    if not query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    return autonomous_agent.memory_search(workspace_root, query, limit=limit)


@app.get("/api/v5/wsil/search")
def web_search_intelligence(query: str, max_results: int = 5) -> Dict[str, Any]:
    state = middleware_stack.before_agent({"query": query, "tool_name": "web_search"})
    if state.get("blocked") or state.get("web_search_allowed") is False:
        raise HTTPException(status_code=400, detail=state.get("warnings", []))
    result = wsil.search(query, max_results=max_results)
    result["middleware_records"] = [
        record.model_dump(mode="json") for record in middleware_stack.records()
    ]
    return result


@app.post("/api/v5/plugins/discover")
def discover_plugins(payload: PluginDiscoverRequest) -> List[PluginManifest]:
    return plugin_runtime.discover(payload.root)


@app.post("/api/v5/plugins/install")
def install_plugin(payload: PluginInstallRequest) -> PluginManifest:
    try:
        return plugin_runtime.install(
            payload.package,
            expected_sha256=payload.expected_sha256,
            overwrite=payload.overwrite,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v5/plugins/sign")
def sign_plugin_manifest(payload: PluginSignRequest) -> Dict[str, Any]:
    try:
        return {"signature": plugin_runtime.sign_manifest(payload.manifest_path, payload.secret)}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v5/plugins/marketplace/load")
def load_plugin_marketplace(payload: MarketplaceLoadRequest) -> Dict[str, Any]:
    try:
        return plugin_runtime.load_marketplace(payload.index_path)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v5/plugins/marketplace")
def list_plugin_marketplace() -> List[Dict[str, Any]]:
    return plugin_runtime.marketplace()


@app.get("/api/v5/plugins")
def list_plugins() -> List[PluginManifest]:
    return plugin_runtime.list_plugins()


@app.get("/api/v5/plugins/capabilities")
def list_plugin_capabilities(
    plugin_id: str | None = None,
    capability_type: str | None = None,
    active_only: bool = False,
) -> List[PluginCapability]:
    return plugin_runtime.list_capabilities(
        plugin_id=plugin_id,
        capability_type=capability_type,
        active_only=active_only,
    )


@app.get("/api/v5/plugins/autonomous-inventory")
def plugin_autonomous_inventory() -> Dict[str, Any]:
    return plugin_runtime.autonomous_inventory()


@app.post("/api/v5/plugins/activate")
def activate_plugin(payload: PluginActivateRequest) -> PluginActivation:
    try:
        return plugin_runtime.activate(payload.plugin_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/plugins/deactivate")
def deactivate_plugin(payload: PluginActivateRequest) -> PluginActivation:
    try:
        return plugin_runtime.deactivate(payload.plugin_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v5/hooks")
def list_hooks() -> List[HookConfig]:
    return hook_runtime.list_hooks()


@app.post("/api/v5/hooks/register")
def register_hook(payload: HookRegisterRequest) -> HookConfig:
    return hook_runtime.register_config(payload.hook)


@app.post("/api/v5/hooks/fire")
def fire_hooks(payload: HookFireRequest) -> List[HookExecutionRecord]:
    return hook_runtime.fire_detailed(payload.event, payload.payload)


@app.get("/api/v5/hooks/records")
def hook_records() -> List[HookExecutionRecord]:
    return hook_runtime.records()


@app.get("/api/v5/skills")
def list_skills(namespace: str | None = None) -> List[SkillDefinition]:
    return skill_runtime.list_skills(namespace=namespace)


@app.post("/api/v5/skills/discover")
def discover_skills(payload: SkillDiscoverRequest) -> List[SkillDefinition]:
    return skill_runtime.discover(payload.root, namespace=payload.namespace)


@app.post("/api/v5/skills/create")
def create_skill(payload: SkillCreateRequest) -> Dict[str, Any]:
    try:
        created = skill_creator.create(
            prompt=payload.prompt,
            name=payload.name,
            description=payload.description,
            root=payload.root,
            namespace=payload.namespace,
            reference_urls=payload.reference_urls,
            allowed_tools=payload.allowed_tools,
            include_python_runner=payload.include_python_runner,
            fetch_references=payload.fetch_references,
            overwrite=payload.overwrite,
        )
        return {
            "skill": created.skill.model_dump(mode="json"),
            "root": created.root,
            "files": created.files,
            "diagnostics": created.diagnostics,
        }
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v5/skills/match")
def match_skills(payload: SkillMatchRequest) -> List[SkillMatch]:
    return skill_runtime.match(payload.text, limit=payload.limit)


@app.get("/api/v5/skills/{skill_id}/context")
def skill_context(skill_id: str) -> Dict[str, Any]:
    try:
        return skill_runtime.invocation_context(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v5/tools")
def list_tools() -> List[ToolRegistration]:
    return tool_gateway.list_tools()


@app.get("/api/v5/middleware/records")
def middleware_records() -> List[MiddlewareRecord]:
    return middleware_stack.records()


@app.post("/api/v5/semantic-cache/put")
def semantic_cache_put(payload: SemanticCachePutRequest) -> Dict[str, Any]:
    semantic_cache.put(
        payload.key,
        payload.query,
        payload.value,
        namespace=payload.namespace,
        metadata=payload.metadata,
        ttl_seconds=payload.ttl_seconds,
    )
    return {"stored": True, "key": payload.key, "namespace": payload.namespace}


@app.post("/api/v5/semantic-cache/lookup")
def semantic_cache_lookup(payload: SemanticCacheLookupRequest) -> SemanticCacheLookup:
    return semantic_cache.lookup(
        payload.query,
        namespace=payload.namespace,
        metadata=payload.metadata,
    )


@app.post("/api/v5/semantic-cache/invalidate")
def semantic_cache_invalidate(payload: SemanticCacheInvalidateRequest) -> Dict[str, Any]:
    return {
        "removed": semantic_cache.invalidate(
            namespace=payload.namespace,
            key=payload.key,
            metadata=payload.metadata,
        )
    }


@app.get("/api/v5/semantic-cache/stats")
def semantic_cache_stats() -> Dict[str, Any]:
    return semantic_cache.stats()


@app.post("/api/v5/ingestion/diagnostics")
def ingestion_diagnostics(payload: IngestionDiagnosticsRequest) -> Dict[str, Any]:
    try:
        artifact = ingestion_pipeline.ingest(DocumentSource(**payload.source))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "document": {
            "document_id": artifact.document.document_id,
            "parser_name": artifact.document.parser_name,
            "media_type": artifact.document.media_type,
            "metadata": artifact.document.metadata,
        },
        "layout": {
            "pages": len(artifact.document.pages),
            "blocks": sum(len(page.blocks) for page in artifact.document.pages),
            "tables": len(artifact.document.tables),
            "ocr_spans": len(artifact.document.ocr_spans),
        },
        "chunks": {
            "strategy": artifact.strategy,
            "count": len(artifact.chunks),
            "average_quality": artifact.metadata.get("average_chunk_quality", 0.0),
        },
    }


# ─── File Upload & RAG Endpoints ────────────────────────────────────────────

@app.post("/api/v5/ingestion/upload")
async def upload_document(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Accept a file upload, run it through the ingestion pipeline, and store for RAG."""
    filename = (file.filename or "upload.bin").strip()
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{ext}'. Allowed: {sorted(_ALLOWED_DOC_EXTENSIONS)}",
        )
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 50 MB.")

    doc_id = str(uuid4())
    # Write to a named temp file so parsers that need a path can read it
    suffix = ext
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        source = DocumentSource(
            document_id=doc_id,
            filename=filename,
            media_type=file.content_type or "",
            path=tmp_path,
            content_base64=base64.b64encode(content).decode(),
        )
        artifact = ingestion_pipeline.ingest(source)
    except Exception as exc:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Ingestion failed: {exc}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    doc_info: Dict[str, Any] = {
        "document_id": doc_id,
        "filename": filename,
        "media_type": file.content_type or artifact.document.media_type,
        "parser_name": artifact.document.parser_name,
        "chunk_count": len(artifact.chunks),
        "page_count": len(artifact.document.pages),
        "table_count": len(artifact.document.tables),
        "average_quality": round(artifact.metadata.get("average_chunk_quality", 0.0), 4),
        "uploaded_at": datetime.datetime.utcnow().isoformat() + "Z",
        "chunks_raw": artifact.chunks,   # ChunkRecord objects kept for retriever
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "text": c.text,
                "quality_score": c.quality_score,
                "metadata": c.metadata,
            }
            for c in artifact.chunks
        ],
    }
    with _document_lock:
        _document_store[doc_id] = doc_info
        _rebuild_rag_index()

    hook_runtime.fire_detailed("DocumentUploaded", {
        "document_id": doc_id,
        "filename": filename,
        "chunk_count": len(artifact.chunks),
    })
    audit_store.append(AuditEvent(
        event_id=str(uuid4()),
        trace_id=doc_id,
        session_id="upload",
        event_type="document_uploaded",
        payload={"document_id": doc_id, "filename": filename, "chunk_count": len(artifact.chunks)},
    ))
    memory_fabric.merge_document(doc_id, {
        "type": "document_reference",
        "document_id": doc_id,
        "filename": filename,
        "summary": f"Uploaded '{filename}' ({len(artifact.chunks)} chunks).",
    })

    response = {k: v for k, v in doc_info.items() if k != "chunks_raw"}
    return response


@app.get("/api/v5/ingestion/documents")
def list_uploaded_documents() -> List[Dict[str, Any]]:
    """Return all documents currently held in the in-memory store."""
    with _document_lock:
        return [
            {k: v for k, v in doc.items() if k != "chunks_raw"}
            for doc in _document_store.values()
        ]


@app.delete("/api/v5/ingestion/documents/{doc_id}")
def delete_uploaded_document(doc_id: str) -> Dict[str, Any]:
    """Remove a document from the store and rebuild the retriever index."""
    with _document_lock:
        if doc_id not in _document_store:
            raise HTTPException(status_code=404, detail="Document not found")
        removed = _document_store.pop(doc_id)
        _rebuild_rag_index()
    hook_runtime.fire_detailed("DocumentDeleted", {
        "document_id": doc_id,
        "filename": removed["filename"],
    })
    audit_store.append(AuditEvent(
        event_id=str(uuid4()),
        trace_id=doc_id,
        session_id="upload",
        event_type="document_deleted",
        payload={"document_id": doc_id, "filename": removed["filename"]},
    ))
    return {"deleted": True, "document_id": doc_id, "filename": removed["filename"]}


@app.post("/api/v5/rag/query")
def rag_query(payload: RAGQueryRequest) -> Dict[str, Any]:
    """Retrieve relevant chunks from uploaded documents and answer the query with context."""
    with _document_lock:
        available_ids = (
            {d for d in payload.document_ids if d in _document_store}
            if payload.document_ids
            else set(_document_store.keys())
        )
        if not _document_store:
            raise HTTPException(status_code=400, detail="No documents uploaded. Upload documents first.")
        if not available_ids:
            raise HTTPException(status_code=400, detail="None of the specified document IDs exist.")

    results = _hybrid_retriever.search(payload.query, top_k=max(payload.top_k, 1))
    filtered = [r for r in results if r.document_id in available_ids]

    citations: List[Dict[str, Any]] = []
    context_parts: List[str] = []
    for idx, result in enumerate(filtered, 1):
        doc = _document_store.get(result.document_id, {})
        context_parts.append(f"[{idx}] From \"{doc.get('filename', result.document_id)}\":\n{result.text}")
        citations.append({
            "index": idx,
            "chunk_id": result.chunk_id,
            "document_id": result.document_id,
            "filename": doc.get("filename", ""),
            "score": round(result.score, 4),
            "scores": {k: round(v, 4) for k, v in result.scores.items()},
            "text_preview": result.text[:300],
        })

    context = "\n\n---\n\n".join(context_parts)

    hook_runtime.fire_detailed("RAGQueryStarted", {
        "query": payload.query,
        "document_ids": list(available_ids),
        "chunks_retrieved": len(filtered),
    })

    result_payload = runtime.submit_query(
        query=payload.query,
        session_id=payload.session_id,
        metadata={
            **payload.metadata,
            "rag_context": context,
            "rag_citations": citations,
            "document_ids": list(available_ids),
        },
    )

    hook_runtime.fire_detailed("RAGQueryCompleted", {
        "query": payload.query,
        "chunks_retrieved": len(filtered),
    })

    return {
        **result_payload,
        "citations": citations,
        "context_used": bool(context),
        "chunks_retrieved": len(filtered),
    }


@app.post("/api/v5/graph/langextract-networkx/build")
def build_langextract_networkx_graph(payload: GraphPipelineRequest) -> Dict[str, Any]:
    try:
        return graph_pipeline.ingest_sources(
            [DocumentSource(**source) for source in payload.sources],
            depth=payload.depth,
            use_provider=payload.use_provider,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v5/graph/networkx")
def get_networkx_graph(version_id: str | None = None) -> Dict[str, Any]:
    return graph_pipeline.graph(version_id)


@app.post("/api/v5/graph/networkx/query")
def query_networkx_graph(payload: GraphQueryRequest) -> Dict[str, Any]:
    return networkx_graph_server.query(
        version_id=payload.version_id,
        node_type=payload.node_type,
        label_contains=payload.label_contains,
        limit=payload.limit,
    )


@app.get("/api/v5/graph/networkx/neighborhood/{node_id}")
def graph_neighborhood(node_id: str, hops: int = 1, version_id: str | None = None) -> Dict[str, Any]:
    return networkx_graph_server.neighborhood(node_id=node_id, hops=hops, version_id=version_id)


@app.get("/api/v5/graph/networkx/lineage/{document_id}")
def graph_lineage(document_id: str) -> Dict[str, Any]:
    return networkx_graph_server.lineage(document_id)


@app.get("/api/v5/graph/status")
def graph_status() -> Dict[str, Any]:
    current = graph_pipeline.graph()
    metrics = current.get("metrics", {})
    return {
        "version_id": current.get("version_id"),
        "node_count": metrics.get("node_count", len(current.get("nodes", []))),
        "edge_count": metrics.get("edge_count", len(current.get("edges", []))),
        "version_count": len(graph_pipeline._graphs),
        "artifact_count": len(graph_pipeline._artifacts),
        "ready": True,
    }


@app.get("/api/v5/workers/state")
def worker_state() -> Dict[str, Any]:
    return {
        "scheduler": scheduler.snapshot(),
        "environment": {
            "cache_backend": semantic_cache.stats()["backend"],
            "worker_mode": os.getenv("OMNI_WORKER_MODE", "in_process"),
            "browser_artifact_dir": os.getenv("OMNI_BROWSER_ARTIFACT_DIR", ".browser-artifacts"),
        },
    }


@app.get("/api/v5/observability")
def observability_snapshot() -> Dict[str, Any]:
    return observability.snapshot()


@app.post("/api/v5/observability/slis")
def record_sli(payload: SLIRecordRequest) -> Dict[str, Any]:
    return observability.record_sli(payload.name, payload.value, payload.labels)


@app.post("/api/v5/observability/slos")
def define_slo(payload: SLODefinitionRequest) -> Dict[str, Any]:
    slo = observability.define_slo(
        SLODefinition(
            name=payload.name,
            sli=payload.sli,
            target=payload.target,
            window_seconds=payload.window_seconds,
            comparator=payload.comparator,
            runbook_id=payload.runbook_id,
        )
    )
    return slo.__dict__


@app.post("/api/v5/observability/runbooks")
def register_runbook(payload: RunbookRegisterRequest) -> Dict[str, Any]:
    runbook = observability.register_runbook(
        Runbook(
            runbook_id=payload.runbook_id,
            title=payload.title,
            steps=payload.steps,
            automation=payload.automation,
        )
    )
    return runbook.__dict__


@app.post("/api/v5/observability/runbooks/{runbook_id}/execute")
def execute_runbook(runbook_id: str) -> Dict[str, Any]:
    try:
        result = observability.runbook(runbook_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    automation = result.get("automation_result", {})
    if automation.get("action") == "recover_worker_leases":
        automation["recovered"] = scheduler.recover_expired_leases()
    elif automation.get("action") == "inspect_cache":
        automation["cache"] = semantic_cache.stats()
    return result


@app.post("/api/v5/workers/lease")
def lease_worker_tasks(payload: WorkerLeaseRequest) -> List[TaskContract]:
    return scheduler.lease_ready(
        payload.worker_id,
        limit=payload.limit,
        lease_seconds=payload.lease_seconds,
    )


@app.post("/api/v5/workers/recover-leases")
def recover_worker_leases() -> Dict[str, Any]:
    return {"recovered": scheduler.recover_expired_leases()}


@app.post("/api/v5/browser/plan")
def plan_browser_automation(payload: BrowserAutomationRequest) -> BrowserActionPlan:
    return browser_planner.plan(payload)


@app.post("/api/v5/browser/execute")
def execute_browser_automation(
    payload: BrowserExecutionRequest,
) -> BrowserExecutionResult:
    result = browser_executor.execute(payload)
    browser_record = browser_sessions.record_execution(
        managed_session_id=payload.session_id,
        execution=result,
    )
    if payload.session_id:
        try:
            managed_sessions.bind_browser(
                payload.session_id, browser_record.browser_session_id
            )
        except KeyError:
            pass
    return result


@app.get("/api/v5/browser/sessions")
def list_browser_sessions() -> List[BrowserSessionRecord]:
    return browser_sessions.list_sessions()


@app.get("/api/v5/browser/sessions/{browser_session_id}")
def get_browser_session(browser_session_id: str) -> BrowserSessionRecord:
    try:
        return browser_sessions.get(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/checkpoints")
def save_checkpoint(payload: CheckpointSaveRequest) -> Checkpoint:
    return checkpoint_store.save(
        payload.state,
        parent_id=payload.parent_id,
        metadata=payload.metadata,
    )


@app.get("/api/v5/checkpoints")
def checkpoint_history() -> List[Checkpoint]:
    return checkpoint_store.history()


@app.post("/api/v5/checkpoints/{checkpoint_id}/branch")
def branch_checkpoint(
    checkpoint_id: str,
    payload: CheckpointBranchRequest,
) -> Checkpoint:
    try:
        return checkpoint_store.branch(
            checkpoint_id,
            payload.updates,
            metadata=payload.metadata,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v5/checkpoints/{checkpoint_id}/lineage")
def checkpoint_lineage(checkpoint_id: str) -> List[Checkpoint]:
    try:
        return checkpoint_store.lineage(checkpoint_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v5/checkpoints/{checkpoint_id}/children")
def checkpoint_children(checkpoint_id: str) -> List[Checkpoint]:
    return checkpoint_store.children(checkpoint_id)


@app.get("/api/v5/checkpoints/diff")
def checkpoint_diff(left_id: str, right_id: str) -> Dict[str, Any]:
    try:
        return checkpoint_store.diff(left_id, right_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/checkpoints/{checkpoint_id}/restore")
def restore_checkpoint(checkpoint_id: str) -> Checkpoint:
    try:
        return checkpoint_store.restore(checkpoint_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/checkpoints/prune")
def prune_checkpoints(keep_last: int = 100) -> Dict[str, Any]:
    return checkpoint_store.prune(keep_last=keep_last)


@app.post("/api/v5/explainability/panel")
def build_explainability_panel(payload: ExplainabilityRequest) -> Dict[str, Any]:
    return explainability_builder.build(
        tasks=payload.tasks,
        retrieval=payload.retrieval,
        graph_paths=payload.graph_paths,
        middleware=payload.middleware,
        hook_records=payload.hook_records,
        cache_records=payload.cache_records,
        confidence_marks=payload.confidence_marks,
    )


@app.post("/api/v5/confidence/watermark")
def confidence_watermark(payload: ConfidenceWatermarkRequest) -> List[Dict[str, Any]]:
    return confidence_watermarker.watermark(
        payload.sentences,
        payload.evidence_scores,
        evidence=payload.evidence,
    )


@app.post("/api/v5/document-intelligence/fingerprint")
def document_fingerprint(payload: DocumentFingerprintRequest) -> Dict[str, Any]:
    return document_dna.fingerprint(payload.text, metadata=payload.metadata)


@app.post("/api/v5/document-intelligence/drift")
def document_drift(payload: DriftCompareRequest) -> Dict[str, Any]:
    return drift_detector.compare(
        payload.previous,
        payload.current,
        review_threshold=payload.review_threshold,
    )


@app.post("/api/v5/document-intelligence/consensus")
def document_consensus(payload: ConsensusRequest) -> Dict[str, Any]:
    return consensus_runtime.decide(
        payload.votes,
        quorum=payload.quorum,
        weight_key=payload.weight_key,
    )


@app.post("/api/v5/agents/{agent_id}/pause")
def pause_agent(agent_id: str) -> AgentRecord:
    try:
        return registry.set_state(agent_id, AgentState.DEGRADED)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/agents/{agent_id}/resume")
def resume_agent(agent_id: str) -> AgentRecord:
    try:
        return registry.set_state(agent_id, AgentState.READY)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/agents/{agent_id}/drain")
def drain_agent(agent_id: str) -> AgentRecord:
    try:
        return registry.set_state(agent_id, AgentState.DRAINING)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/v5/agents/{agent_id}/permissions")
def update_agent_permissions(
    agent_id: str, payload: UpdatePermissionsRequest
) -> AgentRecord:
    try:
        return registry.update_permissions(agent_id, payload.permissions)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/tasks/submit")
def submit_task(payload: SubmitTaskRequest) -> TaskContract:
    return orchestrator.submit_task(payload.task)


@app.get("/api/v5/tasks/metrics")
def task_metrics() -> Dict[str, int]:
    return scheduler.snapshot()


@app.post("/api/v5/tasks/dispatch")
def dispatch_ready(limit: int = 4) -> List[TaskContract]:
    return orchestrator.dispatch_ready(limit=limit)


@app.get("/api/v5/tasks/{task_id}")
def get_task(task_id: UUID) -> TaskContract:
    try:
        return scheduler.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/tasks/{task_id}/done")
def mark_task_done(task_id: UUID) -> TaskContract:
    try:
        return orchestrator.complete_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v5/stream/{session_id}")
def stream_session(session_id: str) -> StreamingResponse:
    def event_iter():
        for event in audit_store.list_events(session_id=session_id):
            yield (
                f"event: {event.event_type}\n"
                f"data: {event.model_dump_json()}\n\n"
            )

    return StreamingResponse(event_iter(), media_type="text/event-stream")


@app.post("/api/v5/memory/query")
def query_memory(payload: MemoryQueryRequest) -> List[Dict[str, Any]]:
    return memory_fabric.query(
        payload.text,
        layers=payload.layers,
        owner_id=payload.owner_id,
        limit=payload.limit,
    )


@app.get("/api/v5/memory/stats")
def memory_stats() -> Dict[str, Any]:
    return memory_fabric.stats()


@app.get("/api/v5/memory/export")
def export_memory() -> Dict[str, Any]:
    return memory_fabric.export_state()


@app.post("/api/v5/memory/import")
def import_memory(payload: MemoryImportRequest) -> Dict[str, Any]:
    return memory_fabric.import_state(payload.state, merge=payload.merge)


@app.get("/api/v5/memory/{session_id}")
def get_memory_snapshot(session_id: str) -> Dict[str, Any]:
    return memory_fabric.get_session_snapshot(session_id)


@app.post("/api/v5/memory/{session_id}/compact")
def compact_memory(session_id: str, payload: MemoryCompactRequest) -> Dict[str, Any]:
    return memory_fabric.compact_session(session_id, keep_last=payload.keep_last)


@app.get("/api/v5/memory/users/{user_id}")
def get_user_memory_snapshot(user_id: str) -> Dict[str, Any]:
    return memory_fabric.get_user_snapshot(user_id)


@app.post("/api/v5/memory/users/{user_id}/long-term")
def merge_long_term_memory(
    user_id: str,
    payload: MemoryMergeRequest,
) -> Dict[str, Any]:
    memory_fabric.merge_long_term(user_id, payload.item)
    return memory_fabric.get_user_snapshot(user_id)


@app.post("/api/v5/memory/users/{user_id}/procedural")
def merge_procedural_memory(
    user_id: str,
    payload: MemoryMergeRequest,
) -> Dict[str, Any]:
    memory_fabric.merge_procedural(user_id, payload.item)
    return memory_fabric.get_user_snapshot(user_id)


@app.get("/api/v5/memory/documents/{document_id}")
def get_document_memory_snapshot(document_id: str) -> Dict[str, Any]:
    return memory_fabric.get_document_snapshot(document_id)


@app.post("/api/v5/memory/documents/{document_id}")
def merge_document_memory(
    document_id: str,
    payload: MemoryMergeRequest,
) -> Dict[str, Any]:
    memory_fabric.merge_document(document_id, payload.item)
    return memory_fabric.get_document_snapshot(document_id)


@app.post("/api/v5/memory/project/load-instructions")
def load_project_memory_instructions(
    payload: ProjectMemoryLoadRequest,
) -> Dict[str, Any]:
    return memory_fabric.load_project_instructions(payload.root)


@app.post("/api/v5/policies/validate")
def validate_policy(payload: PolicyValidationRequest) -> PolicyDecision:
    return orchestrator.validate_task(payload.task)


@app.post("/api/v5/hitl/approve")
def approve_hitl(payload: HitlResolutionRequest) -> HitlRequest:
    try:
        return hitl_store.resolve(
            payload.hitl_id,
            HitlDecision.APPROVED,
            reviewer=payload.reviewer,
            note=payload.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v5/hitl/reject")
def reject_hitl(payload: HitlResolutionRequest) -> HitlRequest:
    try:
        return hitl_store.resolve(
            payload.hitl_id,
            HitlDecision.REJECTED,
            reviewer=payload.reviewer,
            note=payload.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v5/hitl")
def list_hitl_requests() -> List[HitlRequest]:
    return hitl_store.list_requests()


@app.get("/api/v5/audit/events")
def list_audit_events(
    session_id: str | None = None,
    trace_id: str | None = None,
    event_type: str | None = None,
) -> List[AuditEvent]:
    return audit_store.list_events(
        session_id=session_id, trace_id=trace_id, event_type=event_type
    )


@app.post("/api/v5/checkpoints/{checkpoint_id}/replay")
def replay_checkpoint(checkpoint_id: str) -> Dict[str, Any]:
    try:
        return {
            "accepted": True,
            "checkpoint_id": checkpoint_id,
            "state": checkpoint_store.replay(checkpoint_id),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

