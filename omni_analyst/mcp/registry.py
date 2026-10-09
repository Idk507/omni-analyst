from __future__ import annotations

from threading import RLock
from typing import Dict, List

from omni_analyst.mcp.models import MCPServerConfig, ResolvedMCPTool


class MCPRegistry:
    """Central registry for MCP streamable HTTP server configs."""

    def __init__(self) -> None:
        self._servers: Dict[str, MCPServerConfig] = {}
        self._lock = RLock()

    def register_server(self, config: MCPServerConfig) -> MCPServerConfig:
        with self._lock:
            self._servers[config.name] = config
            return config

    def get_server(self, server_name: str) -> MCPServerConfig:
        with self._lock:
            if server_name not in self._servers:
                raise KeyError(f"Unknown MCP server: {server_name}")
            return self._servers[server_name]

    def list_servers(self) -> List[MCPServerConfig]:
        with self._lock:
            return list(self._servers.values())

    def get_tools(self) -> List[ResolvedMCPTool]:
        with self._lock:
            servers = list(self._servers.values())
        resolved: List[ResolvedMCPTool] = []
        for server in servers:
            for tool in server.tools:
                resolved.append(
                    ResolvedMCPTool(
                        qualified_name=f"mcp:{tool.name}",
                        server_name=server.name,
                        tool_name=tool.name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                    )
                )
        return resolved

    def resolve_tool(self, qualified_name: str) -> ResolvedMCPTool:
        for tool in self.get_tools():
            if tool.qualified_name == qualified_name:
                return tool
        raise KeyError(f"Unknown MCP tool: {qualified_name}")


def build_default_registry() -> MCPRegistry:
    """Builds the v4/v5 baseline server registry."""
    from omni_analyst.mcp.models import MCPToolDescriptor

    registry = MCPRegistry()
    registry.register_server(
        MCPServerConfig(
            name="web_search_server",
            url="https://mcp-web-search.internal/invoke",
            interceptor_names=[
                "auth_injector",
                "state_bridge",
                "rate_limiter",
                "audit_logger",
            ],
            tools=[
                MCPToolDescriptor(
                    name="web_search",
                    description="Search the web for fresh evidence and sources.",
                    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                ),
                MCPToolDescriptor(
                    name="web_fetch",
                    description="Fetch a URL and return readable content.",
                    input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
                ),
            ],
        )
    )
    registry.register_server(
        MCPServerConfig(
            name="networkx_graph_server",
            url="local://networkx_graph_server",
            transport="local",
            interceptor_names=[
                "state_bridge",
                "audit_logger",
            ],
            tools=[
                MCPToolDescriptor(
                    name="networkx_graph_ingest",
                    description="Ingest documents, run LangExtract grounding, and build a NetworkX graph.",
                    input_schema={
                        "type": "object",
                        "required": ["sources"],
                        "properties": {
                            "sources": {"type": "array"},
                            "depth": {"type": "string"},
                            "use_provider": {"type": "boolean"},
                        },
                    },
                ),
                MCPToolDescriptor(
                    name="networkx_graph_query",
                    description="Query the current NetworkX graph by node type or label text.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "version_id": {"type": "string"},
                            "node_type": {"type": "string"},
                            "label_contains": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                    },
                ),
                MCPToolDescriptor(
                    name="networkx_graph_neighborhood",
                    description="Return a bounded node neighborhood from the current NetworkX graph.",
                    input_schema={
                        "type": "object",
                        "required": ["node_id"],
                        "properties": {
                            "node_id": {"type": "string"},
                            "version_id": {"type": "string"},
                            "hops": {"type": "integer"},
                            "limit": {"type": "integer"},
                        },
                    },
                ),
                MCPToolDescriptor(
                    name="networkx_graph_lineage",
                    description="Return document graph version lineage.",
                    input_schema={
                        "type": "object",
                        "required": ["document_id"],
                        "properties": {"document_id": {"type": "string"}},
                    },
                ),
            ],
        )
    )
    registry.register_server(
        MCPServerConfig(
            name="database_server",
            url="https://mcp-database.internal/invoke",
            interceptor_names=[
                "auth_injector",
                "state_bridge",
                "rate_limiter",
                "audit_logger",
            ],
            tools=[
                MCPToolDescriptor(
                    name="database_query",
                    description="Execute governed database reads.",
                    input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
                )
            ],
        )
    )
    return registry

