from __future__ import annotations

from typing import Any, Dict

from omni_analyst.mcp.models import MCPInvocationContext, MCPServerConfig


class StateBridgeInterceptor:
    name = "state_bridge"

    def __init__(self, base_state: Dict[str, Any] | None = None) -> None:
        self._base_state = base_state or {}

    def before_request(
        self, context: MCPInvocationContext, server: MCPServerConfig
    ) -> MCPInvocationContext:
        _ = server
        merged = dict(self._base_state)
        merged.update(context.request_context)
        if context.session_id:
            merged.setdefault("session_id", context.session_id)
        if context.user_id:
            merged.setdefault("user_id", context.user_id)
        if context.trace_id:
            merged.setdefault("trace_id", context.trace_id)
        context.request_context = merged
        return context

    def after_response(
        self,
        context: MCPInvocationContext,
        server: MCPServerConfig,
        response_payload: dict,
        status_code: int,
    ) -> dict:
        _ = context, server, status_code
        return response_payload

