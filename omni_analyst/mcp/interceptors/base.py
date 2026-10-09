from __future__ import annotations

from typing import Protocol

from omni_analyst.mcp.models import MCPInvocationContext, MCPServerConfig


class MCPInterceptor(Protocol):
    name: str

    def before_request(
        self, context: MCPInvocationContext, server: MCPServerConfig
    ) -> MCPInvocationContext: ...

    def after_response(
        self,
        context: MCPInvocationContext,
        server: MCPServerConfig,
        response_payload: dict,
        status_code: int,
    ) -> dict: ...

