from __future__ import annotations

from typing import Dict, Iterable, List

from omni_analyst.audit.store import AuditStore
from omni_analyst.mcp.interceptors.audit_logger import AuditLoggerInterceptor
from omni_analyst.mcp.interceptors.auth_injector import AuthInjectorInterceptor
from omni_analyst.mcp.interceptors.base import MCPInterceptor
from omni_analyst.mcp.interceptors.rate_limiter import RateLimiterInterceptor
from omni_analyst.mcp.interceptors.state_bridge import StateBridgeInterceptor


class MCPInterceptorRegistry:
    """Resolves configured interceptor names into executable instances."""

    def __init__(
        self,
        *,
        audit_store: AuditStore,
        credentials: Dict[str, str] | None = None,
        base_state: Dict[str, object] | None = None,
        rate_limit_max_requests: int = 10,
        rate_limit_window_seconds: float = 60.0,
    ) -> None:
        self._interceptors: Dict[str, MCPInterceptor] = {
            "auth_injector": AuthInjectorInterceptor(credentials=credentials),
            "state_bridge": StateBridgeInterceptor(base_state=base_state),
            "rate_limiter": RateLimiterInterceptor(
                max_requests=rate_limit_max_requests,
                window_seconds=rate_limit_window_seconds,
            ),
            "audit_logger": AuditLoggerInterceptor(audit_store=audit_store),
        }

    def get(self, name: str) -> MCPInterceptor:
        if name not in self._interceptors:
            raise KeyError(f"Unknown MCP interceptor: {name}")
        return self._interceptors[name]

    def resolve_many(self, names: Iterable[str]) -> List[MCPInterceptor]:
        return [self.get(name) for name in names]

