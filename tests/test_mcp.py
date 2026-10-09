from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from omni_analyst.audit.store import AuditStore
from omni_analyst.mcp.client import MCPStreamableHttpClient
from omni_analyst.mcp.interceptors.rate_limiter import MCPRateLimitError
from omni_analyst.mcp.interceptors.registry import MCPInterceptorRegistry
from omni_analyst.mcp.models import MCPServerConfig, MCPToolDescriptor
from omni_analyst.mcp.registry import MCPRegistry, build_default_registry


class MCPRegistryTests(unittest.TestCase):
    def test_default_registry_flattens_tools(self) -> None:
        registry = build_default_registry()
        tools = registry.get_tools()

        self.assertGreaterEqual(len(tools), 7)
        self.assertTrue(any(tool.qualified_name == "mcp:web_search" for tool in tools))
        self.assertTrue(any(tool.qualified_name == "mcp:networkx_graph_query" for tool in tools))
        self.assertEqual(registry.get_server("networkx_graph_server").transport, "local")


class MCPClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audit_store = AuditStore()
        self.registry = MCPRegistry()
        self.registry.register_server(
            MCPServerConfig(
                name="web_search_server",
                url="https://mcp.test/invoke",
                interceptor_names=[
                    "auth_injector",
                    "state_bridge",
                    "rate_limiter",
                    "audit_logger",
                ],
                tools=[
                    MCPToolDescriptor(
                        name="web_search",
                        description="Search",
                        input_schema={"type": "object"},
                    )
                ],
            )
        )

    def test_client_invokes_tool_with_auth_and_state_bridge(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                text=json.dumps({"result": {"ok": True, "echo": captured["body"]}}),
            )

        client = MCPStreamableHttpClient(
            self.registry,
            MCPInterceptorRegistry(
                audit_store=self.audit_store,
                credentials={"web_search_server": "secret-token"},
                base_state={"doc_id": "doc-123"},
            ),
            transport=httpx.MockTransport(handler),
        )

        result = asyncio.run(
            client.call_tool(
                server_name="web_search_server",
                tool_name="web_search",
                arguments={"query": "GDPR 2025"},
                session_id="sess-1",
                user_id="user-1",
                trace_id="trace-1",
                request_context={"user_scope": "analyst"},
            )
        )

        self.assertEqual(captured["authorization"], "Bearer secret-token")
        self.assertEqual(captured["body"]["context"]["doc_id"], "doc-123")
        self.assertEqual(captured["body"]["context"]["session_id"], "sess-1")
        self.assertEqual(captured["body"]["context"]["user_scope"], "analyst")
        self.assertTrue(result.result["ok"])
        events = self.audit_store.list_events(session_id="sess-1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "mcp_tool_call_completed")

    def test_rate_limiter_blocks_excess_requests(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=json.dumps({"result": {"ok": True}}))

        client = MCPStreamableHttpClient(
            self.registry,
            MCPInterceptorRegistry(
                audit_store=self.audit_store,
                rate_limit_max_requests=1,
                rate_limit_window_seconds=60.0,
            ),
            transport=httpx.MockTransport(handler),
        )

        asyncio.run(
            client.call_tool(
                server_name="web_search_server",
                tool_name="web_search",
                arguments={"query": "first"},
            )
        )

        with self.assertRaises(MCPRateLimitError):
            asyncio.run(
                client.call_tool(
                    server_name="web_search_server",
                    tool_name="web_search",
                    arguments={"query": "second"},
                )
            )


if __name__ == "__main__":
    unittest.main()
