from __future__ import annotations

from uuid import uuid4

from omni_analyst.audit.store import AuditStore
from omni_analyst.models.contracts import AuditEvent, RiskTier
from omni_analyst.mcp.models import MCPInvocationContext, MCPServerConfig


class AuditLoggerInterceptor:
    name = "audit_logger"

    def __init__(self, audit_store: AuditStore) -> None:
        self.audit_store = audit_store

    def before_request(
        self, context: MCPInvocationContext, server: MCPServerConfig
    ) -> MCPInvocationContext:
        _ = server
        return context

    def after_response(
        self,
        context: MCPInvocationContext,
        server: MCPServerConfig,
        response_payload: dict,
        status_code: int,
    ) -> dict:
        self.audit_store.append(
            AuditEvent(
                event_id=f"evt-{uuid4()}",
                trace_id=context.trace_id or f"trace-{uuid4()}",
                session_id=context.session_id or "session-unknown",
                agent_id=context.user_id,
                event_type="mcp_tool_call_completed",
                risk_tier=RiskTier.R1,
                payload={
                    "server_name": server.name,
                    "tool_name": context.tool_name,
                    "status_code": status_code,
                    "arguments": context.arguments,
                    "result_summary": response_payload,
                },
            )
        )
        return response_payload

