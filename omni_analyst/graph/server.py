from __future__ import annotations

from collections import deque
from typing import Any

from omni_analyst.graph.pipeline import LangExtractNetworkXPipeline
from omni_analyst.ingestion.models import DocumentSource


class NetworkXGraphServer:
    """Local first-class graph MCP server implementation."""

    def __init__(self, pipeline: LangExtractNetworkXPipeline | None = None) -> None:
        self.pipeline = pipeline or LangExtractNetworkXPipeline()

    def ingest(self, sources: list[dict[str, Any]], depth: str = "deep", use_provider: bool = False) -> dict[str, Any]:
        document_sources = [DocumentSource(**source) for source in sources]
        return self.pipeline.ingest_sources(document_sources, depth=depth, use_provider=use_provider)

    def query(
        self,
        version_id: str | None = None,
        node_type: str | None = None,
        label_contains: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        graph = self.pipeline.graph(version_id)
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        if node_type:
            nodes = [node for node in nodes if node.get("node_type") == node_type]
        if label_contains:
            needle = label_contains.lower()
            nodes = [node for node in nodes if needle in str(node.get("label", "")).lower()]
        node_ids = {node["id"] for node in nodes[:limit]}
        return {
            "version_id": graph.get("version_id"),
            "nodes": nodes[:limit],
            "edges": [edge for edge in edges if edge.get("source") in node_ids or edge.get("target") in node_ids][:limit],
            "metrics": graph.get("metrics", {}),
        }

    def neighborhood(self, node_id: str, version_id: str | None = None, hops: int = 1, limit: int = 100) -> dict[str, Any]:
        graph = self.pipeline.graph(version_id)
        edges = graph.get("edges", [])
        adjacency: dict[str, set[str]] = {}
        for edge in edges:
            adjacency.setdefault(edge["source"], set()).add(edge["target"])
            adjacency.setdefault(edge["target"], set()).add(edge["source"])

        visited = {node_id}
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        while queue and len(visited) < limit:
            current, depth = queue.popleft()
            if depth >= hops:
                continue
            for neighbor in adjacency.get(current, set()):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))

        nodes = [node for node in graph.get("nodes", []) if node.get("id") in visited]
        filtered_edges = [
            edge
            for edge in edges
            if edge.get("source") in visited and edge.get("target") in visited
        ][:limit]
        return {"node_id": node_id, "hops": hops, "nodes": nodes, "edges": filtered_edges}

    def lineage(self, document_id: str) -> dict[str, Any]:
        return self.pipeline.lineage(document_id)

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "networkx_graph_ingest",
                "description": "Ingest documents, run LangExtract grounding, and build a NetworkX graph.",
                "input_schema": {
                    "type": "object",
                    "required": ["sources"],
                    "properties": {
                        "sources": {"type": "array"},
                        "depth": {"type": "string"},
                        "use_provider": {"type": "boolean"},
                    },
                },
            },
            {
                "name": "networkx_graph_query",
                "description": "Query the current NetworkX graph by node type or label text.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "version_id": {"type": "string"},
                        "node_type": {"type": "string"},
                        "label_contains": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
            {
                "name": "networkx_graph_neighborhood",
                "description": "Return a bounded node neighborhood from the current NetworkX graph.",
                "input_schema": {
                    "type": "object",
                    "required": ["node_id"],
                    "properties": {
                        "node_id": {"type": "string"},
                        "version_id": {"type": "string"},
                        "hops": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                },
            },
            {
                "name": "networkx_graph_lineage",
                "description": "Return document graph version lineage.",
                "input_schema": {
                    "type": "object",
                    "required": ["document_id"],
                    "properties": {"document_id": {"type": "string"}},
                },
            },
        ]
