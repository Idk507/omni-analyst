from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic
from typing import Deque, Dict

from omni_analyst.mcp.models import MCPInvocationContext, MCPServerConfig


class MCPRateLimitError(RuntimeError):
    """Raised when a server exceeds its configured request budget."""


class RateLimiterInterceptor:
    name = "rate_limiter"

    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, Deque[float]] = defaultdict(deque)

    def before_request(
        self, context: MCPInvocationContext, server: MCPServerConfig
    ) -> MCPInvocationContext:
        now = monotonic()
        bucket = self._requests[server.name]
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            raise MCPRateLimitError(
                f"MCP server '{server.name}' exceeded {self.max_requests} requests "
                f"in {self.window_seconds} seconds"
            )
        bucket.append(now)
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

