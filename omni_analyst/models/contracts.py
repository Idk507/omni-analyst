from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentState(str, Enum):
    DISCOVERED = "DISCOVERED"
    REGISTERED = "REGISTERED"
    READY = "READY"
    BUSY = "BUSY"
    DEGRADED = "DEGRADED"
    DRAINING = "DRAINING"
    OFFLINE = "OFFLINE"


class TaskState(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DONE = "DONE"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


class TaskPriority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"
    BACKGROUND = "BACKGROUND"


class RiskTier(str, Enum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class ExecutionClass(str, Enum):
    INTERACTIVE = "INTERACTIVE"
    BATCH = "BATCH"
    BACKGROUND = "BACKGROUND"


class ResultRoute(str, Enum):
    ORCHESTRATOR = "ORCHESTRATOR"
    MEMORY = "MEMORY"
    FILE = "FILE"
    GRAPH = "GRAPH"
    CONSENSUS = "CONSENSUS"
    STREAM = "STREAM"


class ModelProviderKind(str, Enum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    GOOGLE_GEMINI = "google_gemini"
    AWS_BEDROCK = "aws_bedrock"
    HUGGINGFACE = "huggingface"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"
    LOCAL_ECHO = "local_echo"


class ModelProviderConfig(BaseModel):
    provider_id: str
    kind: ModelProviderKind
    display_name: str
    default_model: str
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    enabled: bool = True
    headers: Dict[str, str] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)


class ModelMessage(BaseModel):
    role: str
    content: str


class ModelRequest(BaseModel):
    provider_id: str
    messages: List[ModelMessage]
    model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 1024
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    provider_id: str
    model: str
    content: str
    raw: Dict[str, Any] = Field(default_factory=dict)
    usage: Dict[str, Any] = Field(default_factory=dict)


class HookEvent(str, Enum):
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    SESSION_START = "SessionStart"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PRE_MODEL_USE = "PreModelUse"
    POST_MODEL_USE = "PostModelUse"
    NOTIFICATION = "Notification"
    STOP = "Stop"


class HookMode(str, Enum):
    OBSERVE = "observe"
    INTERVENE = "intervene"


class HookDecisionAction(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    MODIFY = "modify"
    WARN = "warn"


class HookMatcher(BaseModel):
    tool_name: Optional[str] = None
    event: Optional[str] = None
    path_glob: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HookCommand(BaseModel):
    type: str = "command"
    command: List[str] = Field(default_factory=list)
    timeout_seconds: int = 10
    cwd: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)


class HookDecision(BaseModel):
    action: HookDecisionAction = HookDecisionAction.ALLOW
    reason: str = ""
    payload_updates: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class HookConfig(BaseModel):
    hook_id: str = Field(default_factory=lambda: f"hook-{uuid4()}")
    event: HookEvent | str
    name: str
    matcher: HookMatcher = Field(default_factory=HookMatcher)
    mode: HookMode = HookMode.OBSERVE
    priority: int = 100
    composable: bool = True
    fail_closed: bool = False
    source: str = "runtime"
    command: Optional[HookCommand] = None
    enabled: bool = True


class HookExecutionRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: f"hook-run-{uuid4()}")
    hook_id: str
    event: str
    name: str
    matched: bool = True
    allowed: bool = True
    decision: HookDecision = Field(default_factory=HookDecision)
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    timed_out: bool = False
    duration_ms: int = 0
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class PluginTrustLevel(str, Enum):
    UNTRUSTED = "untrusted"
    PROJECT = "project"
    USER = "user"
    SYSTEM = "system"


class PluginPermission(BaseModel):
    name: str
    scope: str = "plugin"
    allowed: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PluginCapabilityType(str, Enum):
    TOOL = "tool"
    SKILL = "skill"
    HOOK = "hook"
    COMMAND = "command"
    MCP_SERVER = "mcp_server"
    MODEL_PROVIDER = "model_provider"
    WEB_SEARCH_PROVIDER = "web_search_provider"
    CHANNEL = "channel"
    SUBAGENT = "subagent"
    MEDIA = "media"
    PERMISSION = "permission"


class PluginCapability(BaseModel):
    capability_id: str
    plugin_id: str
    type: PluginCapabilityType
    name: str
    namespace: str
    description: str = ""
    enabled: bool = True
    risk_tier: RiskTier = RiskTier.R0
    source_path: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PluginActivation(BaseModel):
    plugin_id: str
    active: bool
    namespace: str
    loaded_hooks: List[str] = Field(default_factory=list)
    loaded_tools: List[str] = Field(default_factory=list)
    loaded_skills: List[str] = Field(default_factory=list)
    diagnostics: List[str] = Field(default_factory=list)
    activated_at: datetime = Field(default_factory=_now)


class SkillDefinition(BaseModel):
    skill_id: str = Field(default_factory=lambda: f"skill-{uuid4()}")
    name: str
    description: str = ""
    path: str = ""
    namespace: str = "default"
    triggers: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    resources: List[str] = Field(default_factory=list)
    content: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SkillMatch(BaseModel):
    skill: SkillDefinition
    score: float
    reasons: List[str] = Field(default_factory=list)


class ToolRegistration(BaseModel):
    tool_name: str
    source: str = "local"
    namespace: str = "default"
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    permissions: Dict[str, Any] = Field(default_factory=dict)
    risk_tier: RiskTier = RiskTier.R0
    timeout_seconds: int = 30
    enabled: bool = True


class ToolExecutionResult(BaseModel):
    tool_name: str
    provider: str
    ok: bool
    result: Dict[str, Any] = Field(default_factory=dict)
    chunks: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    hook_records: List[HookExecutionRecord] = Field(default_factory=list)
    duration_ms: int = 0


class MiddlewarePhase(str, Enum):
    BEFORE_AGENT = "before_agent"
    BEFORE_MODEL = "before_model"
    AFTER_MODEL = "after_model"
    AFTER_AGENT = "after_agent"
    WRAP_MODEL_CALL = "wrap_model_call"
    WRAP_TOOL_CALL = "wrap_tool_call"


class MiddlewareRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: f"mw-{uuid4()}")
    name: str
    phase: MiddlewarePhase | str
    allowed: bool = True
    state_updates: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    duration_ms: int = 0
    created_at: datetime = Field(default_factory=_now)


class SemanticCacheEntry(BaseModel):
    key: str
    namespace: str = "default"
    query: str
    value: Dict[str, Any]
    embedding: List[float] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: Optional[int] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SemanticCacheLookup(BaseModel):
    cache_hit: bool
    key: Optional[str] = None
    similarity: float = 0.0
    value: Optional[Dict[str, Any]] = None
    stale: bool = False


class RalphModeStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class RalphModeRequest(BaseModel):
    prompt: str = Field(min_length=1)
    workspace_root: str = "."
    run_dir: str = ".omni_memory/ralph_runs"
    iterations: int = 5
    agent_command: List[str] = Field(default_factory=list)
    verification_commands: List[List[str]] = Field(default_factory=list)
    timeout_seconds: int = 300
    stop_on_success: bool = True
    success_markers: List[str] = Field(default_factory=lambda: ["RALPH_DONE", "DONE"])
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RalphIterationRecord(BaseModel):
    iteration: int
    prompt_path: str
    status: RalphModeStatus = RalphModeStatus.PENDING
    fresh_context: bool = True
    checkpoint_id: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    duration_ms: int = 0
    verification: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None


class RalphModeRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"ralph-{uuid4()}")
    request: RalphModeRequest
    status: RalphModeStatus = RalphModeStatus.PENDING
    run_root: str = ""
    prompt_path: str = ""
    state_path: str = ""
    progress_path: str = ""
    iterations: List[RalphIterationRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    summary: Dict[str, Any] = Field(default_factory=dict)


class DeepAgentStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DeepAgentTodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class DeepAgentTodo(BaseModel):
    id: str = Field(default_factory=lambda: f"todo-{uuid4()}")
    content: str
    status: DeepAgentTodoStatus = DeepAgentTodoStatus.PENDING


class FilesystemPermissionMode(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class FilesystemPermissionRule(BaseModel):
    operations: List[str] = Field(default_factory=list)
    paths: List[str] = Field(default_factory=list)
    mode: FilesystemPermissionMode = FilesystemPermissionMode.ALLOW


class VirtualFileOperation(BaseModel):
    operation: str
    path: str
    content: Optional[str] = None
    old_string: Optional[str] = None
    new_string: Optional[str] = None
    glob_pattern: Optional[str] = None
    offset: int = 0
    limit: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeepAgentToolCall(BaseModel):
    tool_call_id: str = Field(default_factory=lambda: f"tool-call-{uuid4()}")
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: int = 0


class DeepAgentInterrupt(BaseModel):
    interrupt_id: str = Field(default_factory=lambda: f"deep-interrupt-{uuid4()}")
    run_id: str
    step_index: int
    action_requests: List[DeepAgentToolCall] = Field(default_factory=list)
    review_configs: List[Dict[str, Any]] = Field(default_factory=list)
    allowed_decisions: List[str] = Field(default_factory=lambda: ["approve", "edit", "reject", "respond"])
    status: str = "PENDING"
    decisions: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    resolved_at: Optional[datetime] = None


class DeepSubagentSpec(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: List[str] = Field(default_factory=list)
    permissions: List[FilesystemPermissionRule] = Field(default_factory=list)
    interrupt_on: Dict[str, Any] = Field(default_factory=dict)
    max_steps: int = 3


class AsyncSubagentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


class AsyncDeepSubagentSpec(BaseModel):
    name: str
    description: str
    graph_id: str
    url: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    tools: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AsyncSubagentTask(BaseModel):
    task_id: str = Field(default_factory=lambda: f"async-task-{uuid4()}")
    agent_name: str
    graph_id: str
    thread_id: str
    run_id: str
    task: str
    status: AsyncSubagentStatus = AsyncSubagentStatus.PENDING
    result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    updates: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    started_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    last_updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class DeepAgentStep(BaseModel):
    step_index: int
    messages: List[ModelMessage] = Field(default_factory=list)
    model_response: Optional[ModelResponse] = None
    tool_calls: List[DeepAgentToolCall] = Field(default_factory=list)
    todos: List[DeepAgentTodo] = Field(default_factory=list)
    checkpoint_id: Optional[str] = None
    interrupt: Optional[DeepAgentInterrupt] = None
    status: DeepAgentStatus = DeepAgentStatus.PENDING
    started_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None


class DeepAgentRequest(BaseModel):
    messages: List[ModelMessage]
    provider_id: str = "local_echo"
    model: Optional[str] = None
    system_prompt: str = "You are a deep agent. Plan, use tools, verify, and summarize."
    workspace_root: str = "."
    backend: str = "local"
    max_steps: int = 5
    tool_allowlist: List[str] = Field(default_factory=list)
    permissions: List[FilesystemPermissionRule] = Field(default_factory=list)
    interrupt_on: Dict[str, Any] = Field(default_factory=dict)
    subagents: List[DeepSubagentSpec] = Field(default_factory=list)
    async_subagents: List[AsyncDeepSubagentSpec] = Field(default_factory=list)
    use_skills: bool = True
    memory_files: List[str] = Field(default_factory=list)
    sandbox_enabled: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeepAgentRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"deep-run-{uuid4()}")
    request: DeepAgentRequest
    status: DeepAgentStatus = DeepAgentStatus.PENDING
    steps: List[DeepAgentStep] = Field(default_factory=list)
    final_answer: str = ""
    todos: List[DeepAgentTodo] = Field(default_factory=list)
    interrupts: List[DeepAgentInterrupt] = Field(default_factory=list)
    checkpoint_id: Optional[str] = None
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    summary: Dict[str, Any] = Field(default_factory=dict)


class AutonomousSandboxMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class AutonomousApprovalPolicy(str, Enum):
    UNTRUSTED = "untrusted"
    ON_REQUEST = "on-request"
    NEVER = "never"
    GRANULAR = "granular"


class AutonomousAgentConfig(BaseModel):
    provider_id: str = "local_echo"
    model: Optional[str] = None
    sandbox_mode: AutonomousSandboxMode = AutonomousSandboxMode.WORKSPACE_WRITE
    approval_policy: AutonomousApprovalPolicy = AutonomousApprovalPolicy.ON_REQUEST
    writable_roots: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    profile: str = "default"
    project_doc_max_bytes: int = 32768
    raw: Dict[str, Any] = Field(default_factory=dict)


class AutonomousAgentRequest(BaseModel):
    prompt: str = Field(min_length=1)
    workspace_root: str = "."
    config_root: Optional[str] = None
    provider_id: Optional[str] = None
    model: Optional[str] = None
    profile: str = "default"
    max_steps: int = 5
    learn_from_run: bool = True
    verification_commands: List[List[str]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AutonomousAgentRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"auto-run-{uuid4()}")
    request: AutonomousAgentRequest
    config: AutonomousAgentConfig
    deep_run: DeepAgentRun
    instruction_files: List[Dict[str, Any]] = Field(default_factory=list)
    memory_updates: List[Dict[str, Any]] = Field(default_factory=list)
    created_skill: Optional[SkillDefinition] = None
    verification: List[Dict[str, Any]] = Field(default_factory=list)
    status: DeepAgentStatus = DeepAgentStatus.PENDING
    created_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    summary: Dict[str, Any] = Field(default_factory=dict)


class AgentHealth(BaseModel):
    state: AgentState = AgentState.REGISTERED
    last_heartbeat: datetime = Field(default_factory=_now)
    error_rate_5m: float = 0.0
    p95_latency_ms: int = 0


class AgentCapacity(BaseModel):
    concurrency_limit: int = 1
    current_load: int = 0


class AgentRecord(BaseModel):
    agent_id: str
    agent_type: str
    version: str = "0.1.0"
    provider: str = "core"
    capabilities: List[str] = Field(default_factory=list)
    permissions: Dict[str, Any] = Field(default_factory=dict)
    health: AgentHealth = Field(default_factory=AgentHealth)
    capacity: AgentCapacity = Field(default_factory=AgentCapacity)


class SubagentProfile(BaseModel):
    profile_id: str
    name: str
    role: str
    description: str
    system_prompt: str
    tools: List[str] = Field(default_factory=list)
    permissions: Dict[str, Any] = Field(default_factory=dict)
    model_hint: Optional[str] = None
    context_policy: str = "isolated"


class ManagedAgentSession(BaseModel):
    session_id: str
    user_goal: str
    active_agent_id: Optional[str] = None
    subagent_ids: List[str] = Field(default_factory=list)
    sandbox_id: Optional[str] = None
    browser_session_id: Optional[str] = None
    state: str = "ACTIVE"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentManagementSnapshot(BaseModel):
    agents: List[AgentRecord] = Field(default_factory=list)
    subagent_profiles: List[SubagentProfile] = Field(default_factory=list)
    sessions: List[ManagedAgentSession] = Field(default_factory=list)
    environment: EnvironmentSnapshot | None = None
    summary: Dict[str, Any] = Field(default_factory=dict)


class EnvironmentSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: f"env-{uuid4()}")
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    python_version: Optional[str] = None
    platform: Optional[str] = None
    available_tools: List[str] = Field(default_factory=list)
    sandbox_active: bool = False
    browser_active: bool = False
    health: str = "UNKNOWN"
    warnings: List[str] = Field(default_factory=list)
    captured_at: datetime = Field(default_factory=_now)


class SandboxCommand(BaseModel):
    command: List[str]
    cwd: Optional[str] = None
    timeout_seconds: int = 30
    env: Dict[str, str] = Field(default_factory=dict)


class SandboxResult(BaseModel):
    sandbox_id: str
    command: List[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    evaluation: Dict[str, Any] = Field(default_factory=dict)


class CodePipelineRequest(BaseModel):
    goal: str
    repository_path: str = "."
    changed_files: List[str] = Field(default_factory=list)
    test_commands: List[List[str]] = Field(default_factory=list)
    sandbox_required: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CodePipelineStage(BaseModel):
    name: str
    agent_id: str
    status: str = "PENDING"
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    sandbox_results: List[SandboxResult] = Field(default_factory=list)
    output: Dict[str, Any] = Field(default_factory=dict)


class CodePipelineRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"code-run-{uuid4()}")
    request: CodePipelineRequest
    stages: List[CodePipelineStage] = Field(default_factory=list)
    status: str = "PENDING"
    created_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None


class BrowserAutomationRequest(BaseModel):
    goal: str
    url: Optional[str] = None
    screenshot_base64: Optional[str] = None
    page_snapshot: Optional[str] = None
    constraints: Dict[str, Any] = Field(default_factory=dict)
    max_steps: int = 8


class BrowserActionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"browser-plan-{uuid4()}")
    goal: str
    perception_mode: str
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    verification: Dict[str, Any] = Field(default_factory=dict)
    requires_browser: bool = True
    risk_tier: RiskTier = RiskTier.R1


class BrowserNodeSnapshot(BaseModel):
    ref: str
    role: str = ""
    name: str = ""
    text: str = ""
    selector: str = ""
    visible: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BrowserPageSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: f"browser-snapshot-{uuid4()}")
    url: str = ""
    title: str = ""
    text: str = ""
    nodes: List[BrowserNodeSnapshot] = Field(default_factory=list)
    screenshot_path: Optional[str] = None
    console_messages: List[Dict[str, Any]] = Field(default_factory=list)
    network_events: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class BrowserExecutionRequest(BaseModel):
    plan: BrowserActionPlan
    session_id: Optional[str] = None
    headless: bool = True
    dry_run: bool = False
    artifact_dir: str = ".browser-artifacts"
    timeout_seconds: int = 30


class BrowserStepResult(BaseModel):
    action: Dict[str, Any]
    status: str
    message: str = ""
    url: Optional[str] = None
    snapshot: Optional[str] = None
    page_snapshot: Optional[BrowserPageSnapshot] = None
    verified: bool = False
    retry_count: int = 0
    artifacts: List[str] = Field(default_factory=list)


class BrowserExecutionResult(BaseModel):
    execution_id: str = Field(default_factory=lambda: f"browser-exec-{uuid4()}")
    plan_id: str
    session_id: Optional[str] = None
    mode: str = "dry_run"
    status: str = "PENDING"
    steps: List[BrowserStepResult] = Field(default_factory=list)
    artifacts: List[str] = Field(default_factory=list)
    evaluation: Dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None


class BrowserSessionRecord(BaseModel):
    browser_session_id: str = Field(default_factory=lambda: f"browser-session-{uuid4()}")
    managed_session_id: Optional[str] = None
    latest_execution_id: Optional[str] = None
    status: str = "ACTIVE"
    artifact_paths: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class RetryPolicy(BaseModel):
    max_retries: int = 0
    backoff_strategy: str = "EXPONENTIAL"
    backoff_base_ms: int = 250
    fallback_agent: Optional[str] = None
    retry_condition: str = "ON_ERROR"
    escalate_after: int = 0


class BudgetGuard(BaseModel):
    max_token_estimate: int = 0
    max_cost_usd: float = 0.0


class TaskContract(BaseModel):
    task_id: UUID = Field(default_factory=uuid4)
    task_type: str
    created_by: str
    assigned_to: Optional[str] = None
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskState = TaskState.PENDING
    risk_tier: RiskTier = RiskTier.R0
    execution_class: ExecutionClass = ExecutionClass.INTERACTIVE
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[UUID] = Field(default_factory=list)
    timeout_seconds: int = 300
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    budget_guard: BudgetGuard = Field(default_factory=BudgetGuard)
    created_at: datetime = Field(default_factory=_now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    checkpoint_id: Optional[str] = None
    hitl_required: bool = False
    cost_estimate: float = 0.0
    result_routes: List[ResultRoute] = Field(
        default_factory=lambda: [ResultRoute.ORCHESTRATOR]
    )
    retry_count: int = 0
    trace_id: Optional[str] = None
    session_id: Optional[str] = None


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str
    risk_tier: RiskTier
    requires_hitl: bool = False
    policy_chain: List[str] = Field(default_factory=list)


class ProvenanceRef(BaseModel):
    task_id: UUID
    agent_id: str
    tool_call_id: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: float = 0.0


class AuditEvent(BaseModel):
    event_id: str
    trace_id: str
    session_id: str
    task_id: Optional[UUID] = None
    agent_id: Optional[str] = None
    event_type: str
    risk_tier: RiskTier = RiskTier.R0
    timestamp: datetime = Field(default_factory=_now)
    payload: Dict[str, Any] = Field(default_factory=dict)
    payload_hash: Optional[str] = None
    previous_event_hash: Optional[str] = None


class HitlDecision(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class HitlRequest(BaseModel):
    hitl_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    trace_id: str
    session_id: str
    reason: str
    risk_tier: RiskTier
    action_summary: str
    status: HitlDecision = HitlDecision.PENDING
    requested_at: datetime = Field(default_factory=_now)
    resolved_at: Optional[datetime] = None
    reviewer: Optional[str] = None
    resolution_note: Optional[str] = None

