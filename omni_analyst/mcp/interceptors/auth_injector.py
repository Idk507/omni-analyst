from __future__ import annotations

import os
from typing import Dict

from omni_analyst.mcp.models import MCPInvocationContext, MCPServerConfig


class AuthInjectorInterceptor:
    name = "auth_injector"

    def __init__(self, credentials: Dict[str, str] | None = None) -> None:
        self._credentials = credentials or {}

    def before_request(
        self, context: MCPInvocationContext, server: MCPServerConfig
    ) -> MCPInvocationContext:
        token = self._credentials.get(server.name)
        if token is None and server.auth_token_env:
            token = os.getenv(server.auth_token_env)
        if token:
            context.headers["Authorization"] = f"Bearer {token}"
        for key, value in server.headers.items():
            context.headers.setdefault(key, value)
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

