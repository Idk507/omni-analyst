from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List

from omni_analyst.hooks.runtime import HookRuntime
from omni_analyst.mcp.client import MCPStreamableHttpClient
from omni_analyst.models.contracts import HookEvent, HookExecutionRecord, ToolRegistration


ToolHandler = Callable[..., Any] | Callable[..., Awaitable[Any]]


@dataclass
class ToolGatewayResult:
    tool_name: str
    provider: str
    result: Dict[str, Any]
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    hook_records: List[HookExecutionRecord] = field(default_factory=list)
    duration_ms: int = 0


class ToolGateway:
    """Dispatches local, plugin, and MCP tools with validation and hooks."""

    def __init__(
        self,
        mcp_client: MCPStreamableHttpClient | None = None,
        hook_runtime: HookRuntime | None = None,
        default_timeout_seconds: int = 30,
    ) -> None:
        self._mcp_client = mcp_client
        self._hook_runtime = hook_runtime
        self._default_timeout_seconds = default_timeout_seconds
        self._local_tools: Dict[str, ToolHandler] = {}
        self._aliases: Dict[str, str] = {}
        self._metadata: Dict[str, ToolRegistration] = {}

    def register_local_tool(
        self,
        name: str,
        handler: ToolHandler,
        metadata: ToolRegistration | None = None,
    ) -> None:
        self._local_tools[name] = handler
        self._metadata[name] = metadata or ToolRegistration(tool_name=name, source="local")

    def register_alias(self, name: str, target: str) -> None:
        self._aliases[name] = target

    def register_tool_metadata(self, metadata: ToolRegistration) -> None:
        self._metadata[metadata.tool_name] = metadata

    def unregister_tool(self, name: str) -> None:
        self._local_tools.pop(name, None)
        self._metadata.pop(name, None)
        for alias, target in list(self._aliases.items()):
            if alias == name or target == name:
                self._aliases.pop(alias, None)

    def list_tools(self) -> List[ToolRegistration]:
        return sorted(self._metadata.values(), key=lambda tool: tool.tool_name)

    async def call(
        self,
        tool_name: str,
        arguments: Dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        request_context: Dict[str, Any] | None = None,
    ) -> ToolGatewayResult:
        started = time.perf_counter()
        resolved_name = self._aliases.get(tool_name, tool_name)
        payload = arguments or {}
        metadata = self._metadata.get(resolved_name)
        hook_payload = {
            "tool_name": resolved_name,
            "arguments": payload,
            "session_id": session_id,
            "user_id": user_id,
            "trace_id": trace_id,
            "metadata": request_context or {},
        }

        hook_records = self._fire_hooks(HookEvent.PRE_TOOL_USE, hook_payload)
        blocked = next((record for record in hook_records if not record.allowed), None)
        if blocked:
            return ToolGatewayResult(
                tool_name=resolved_name,
                provider="blocked",
                result={},
                ok=False,
                error=blocked.decision.reason or "Blocked by PreToolUse hook",
                hook_records=hook_records,
                duration_ms=self._duration(started),
            )

        validation_error = self._validate_arguments(metadata, payload)
        if validation_error:
            return self._failure_result(
                resolved_name, "validation", validation_error, hook_records, hook_payload, started
            )

        try:
            timeout = metadata.timeout_seconds if metadata else self._default_timeout_seconds
            if resolved_name in self._local_tools:
                result = await asyncio.wait_for(
                    self._call_local(resolved_name, payload), timeout=timeout
                )
                normalized = result if isinstance(result, dict) else {"value": result}
                post_records = self._fire_hooks(
                    HookEvent.POST_TOOL_USE, {**hook_payload, "result": normalized}
                )
                return ToolGatewayResult(
                    tool_name=resolved_name,
                    provider="local",
                    result=normalized,
                    hook_records=hook_records + post_records,
                    duration_ms=self._duration(started),
                )

            if not resolved_name.startswith("mcp:"):
                return self._failure_result(
                    resolved_name,
                    "unknown",
                    f"Unknown tool: {tool_name}",
                    hook_records,
                    hook_payload,
                    started,
                )
            if self._mcp_client is None:
                return self._failure_result(
                    resolved_name,
                    "mcp",
                    f"MCP client is not configured for {tool_name}",
                    hook_records,
                    hook_payload,
                    started,
                )

            mcp_result = await asyncio.wait_for(
                self._mcp_client.call_qualified_tool(
                    resolved_name,
                    arguments=payload,
                    session_id=session_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    request_context=request_context,
                ),
                timeout=timeout,
            )
            result_payload = (
                mcp_result.result
                if isinstance(mcp_result.result, dict)
                else {"value": mcp_result.result}
            )
            post_records = self._fire_hooks(
                HookEvent.POST_TOOL_USE, {**hook_payload, "result": result_payload}
            )
            return ToolGatewayResult(
                tool_name=resolved_name,
                provider="mcp",
                result=result_payload,
                chunks=list(mcp_result.chunks),
                hook_records=hook_records + post_records,
                duration_ms=self._duration(started),
            )
        except Exception as exc:
            return self._failure_result(
                resolved_name, "exception", str(exc), hook_records, hook_payload, started
            )

    async def _call_local(self, resolved_name: str, payload: Dict[str, Any]) -> Any:
        handler = self._local_tools[resolved_name]
        result = handler(**payload)
        if inspect.isawaitable(result):
            return await result
        return result

    def _validate_arguments(
        self, metadata: ToolRegistration | None, payload: Dict[str, Any]
    ) -> str | None:
        if metadata is None:
            return None
        if not metadata.enabled:
            return f"Tool is disabled: {metadata.tool_name}"
        if not metadata.input_schema:
            return None
        required = metadata.input_schema.get("required", [])
        missing = [name for name in required if name not in payload]
        if missing:
            return f"Missing required arguments: {', '.join(missing)}"
        properties = metadata.input_schema.get("properties", {})
        for name, schema in properties.items():
            if name not in payload or "type" not in schema:
                continue
            if not self._type_matches(payload[name], str(schema["type"])):
                return f"Argument {name} must be {schema['type']}"
        return None

    def _type_matches(self, value: Any, expected: str) -> bool:
        mapping: dict[str, Any] = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        python_type = mapping.get(expected)
        return True if python_type is None else isinstance(value, python_type)

    def _failure_result(
        self,
        tool_name: str,
        provider: str,
        error: str,
        hook_records: List[HookExecutionRecord],
        hook_payload: Dict[str, Any],
        started: float,
    ) -> ToolGatewayResult:
        failure_records = self._fire_hooks(
            HookEvent.POST_TOOL_USE_FAILURE, {**hook_payload, "error": error}
        )
        return ToolGatewayResult(
            tool_name=tool_name,
            provider=provider,
            result={},
            ok=False,
            error=error,
            hook_records=hook_records + failure_records,
            duration_ms=self._duration(started),
        )

    def _fire_hooks(
        self, event: HookEvent, payload: Dict[str, Any]
    ) -> List[HookExecutionRecord]:
        if self._hook_runtime is None:
            return []
        return self._hook_runtime.fire_detailed(event, payload)

    def _duration(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
