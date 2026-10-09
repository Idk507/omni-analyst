from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

import networkx as nx  # type: ignore[import-untyped]

from omni_analyst.ingestion.models import IngestionArtifact
from omni_analyst.extraction.langextract_runner import LangExtractRunner


class SimpleGraphBuilder:
    """Builds NetworkX graph artifacts from grounded document extractions."""

    def __init__(self, extractor: LangExtractRunner | None = None) -> None:
        self.extractor = extractor or LangExtractRunner()

    def build(self, artifacts: List[IngestionArtifact], *, depth: str = "shallow") -> Dict[str, Any]:
        graph = nx.DiGraph()
        version_id = f"graph-version-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

        for artifact in artifacts:
            document_id = artifact.document.document_id
            document_version = str(artifact.document.metadata.get("document_version") or version_id)
            graph.add_node(
                document_id,
                label=artifact.document.filename,
                node_type="document",
                parser=artifact.document.parser_name,
                version_id=document_version,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            extraction = artifact.metadata.get("langextract") or self.extractor.extract(
                artifact.document.text,
                use_provider=False,
            )

            for chunk in artifact.chunks:
                chunk_id = chunk.chunk_id
                graph.add_node(
                    chunk_id,
                    label=chunk_id,
                    node_type="chunk",
                    document_id=document_id,
                    quality_score=chunk.quality_score,
                    version_id=document_version,
                )
                graph.add_edge(document_id, chunk_id, relation="CONTAINS")

                entity_ids = self._add_grounded_entities(
                    graph,
                    document_id,
                    chunk_id,
                    chunk.text,
                    extraction,
                    document_version,
                )
                if not entity_ids:
                    entity_ids = self._add_keyword_entities(graph, document_id, chunk_id, chunk.text, document_version)

                if depth == "deep":
                    self._add_extracted_relations(graph, document_id, extraction, chunk_id, document_version)
                    self._add_deep_relations(graph, entity_ids, chunk_id)
                else:
                    self._add_shallow_relations(graph, entity_ids, chunk_id)

        return {
            "depth": depth,
            "version_id": version_id,
            "nodes": self._node_payloads(graph),
            "edges": self._edge_payloads(graph),
            "metrics": {
                "node_count": graph.number_of_nodes(),
                "edge_count": graph.number_of_edges(),
                "weakly_connected_components": nx.number_weakly_connected_components(
                    graph
                )
                if graph.number_of_nodes()
                else 0,
            },
        }

    def to_networkx(self, artifacts: List[IngestionArtifact], *, depth: str = "shallow") -> nx.DiGraph:
        payload = self.build(artifacts, depth=depth)
        graph = nx.DiGraph()
        for node in payload["nodes"]:
            node_id = node["id"]
            attributes = {key: value for key, value in node.items() if key != "id"}
            graph.add_node(node_id, **attributes)
        for edge in payload["edges"]:
            source = edge["source"]
            target = edge["target"]
            attributes = {
                key: value
                for key, value in edge.items()
                if key not in {"source", "target"}
            }
            graph.add_edge(source, target, **attributes)
        return graph

    def _node_payloads(self, graph: nx.DiGraph) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for node_id in graph.nodes:
            payloads.append({"id": node_id, **graph.nodes[node_id]})
        return payloads

    def _edge_payloads(self, graph: nx.DiGraph) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for source, target in graph.edges:
            payloads.append(
                {"source": source, "target": target, **graph.edges[source, target]}
            )
        return payloads

    def build_shallow(self, artifacts: List[IngestionArtifact]) -> Dict[str, Any]:
        return self.build(artifacts, depth="shallow")

    def build_deep(self, artifacts: List[IngestionArtifact]) -> Dict[str, Any]:
        return self.build(artifacts, depth="deep")

    def _add_grounded_entities(
        self,
        graph: nx.DiGraph,
        document_id: str,
        chunk_id: str,
        chunk_text: str,
        extraction: Dict[str, Any],
        version_id: str,
    ) -> list[str]:
        entity_ids: list[str] = []
        for entity in extraction.get("entities", []):
            if entity.get("label") not in {"entity", "date", "amount"}:
                continue
            value = str(entity.get("value", "")).strip()
            if not value or value.lower() not in chunk_text.lower():
                continue
            entity_id = f"{document_id}:entity:{self._slug(value)}"
            entity_ids.append(entity_id)
            if entity_id not in graph:
                graph.add_node(
                    entity_id,
                    label=value,
                    node_type=entity.get("label", "entity"),
                    document_id=document_id,
                    frequency=0,
                    version_id=version_id,
                    char_start=entity.get("char_start", -1),
                    char_end=entity.get("char_end", -1),
                    confidence=entity.get("confidence", 0.0),
                    temporal_valid_from=datetime.now(timezone.utc).isoformat(),
                )
            graph.nodes[entity_id]["frequency"] += 1
            graph.add_edge(
                chunk_id,
                entity_id,
                relation="MENTIONS",
                weight=1,
                confidence=entity.get("confidence", 0.0),
                char_start=entity.get("char_start", -1),
                char_end=entity.get("char_end", -1),
                version_id=version_id,
            )
        return list(dict.fromkeys(entity_ids))

    def _add_keyword_entities(
        self,
        graph: nx.DiGraph,
        document_id: str,
        chunk_id: str,
        text: str,
        version_id: str,
    ) -> list[str]:
        entity_ids: list[str] = []
        for keyword, frequency in self._keywords(text).items():
            keyword_id = f"{document_id}:entity:{keyword}"
            entity_ids.append(keyword_id)
            if keyword_id not in graph:
                graph.add_node(
                    keyword_id,
                    label=keyword,
                    node_type="entity",
                    document_id=document_id,
                    frequency=0,
                    version_id=version_id,
                    confidence=0.35,
                )
            graph.nodes[keyword_id]["frequency"] += frequency
            graph.add_edge(chunk_id, keyword_id, relation="MENTIONS", weight=frequency, version_id=version_id)
        return entity_ids

    def _add_extracted_relations(
        self,
        graph: nx.DiGraph,
        document_id: str,
        extraction: Dict[str, Any],
        chunk_id: str,
        version_id: str,
    ) -> None:
        for relation in extraction.get("relations", []):
            source = str(relation.get("source", "")).strip()
            target = str(relation.get("target", "")).strip()
            if not source or not target:
                continue
            source_id = f"{document_id}:entity:{self._slug(source)}"
            target_id = f"{document_id}:entity:{self._slug(target)}"
            for node_id, label in [(source_id, source), (target_id, target)]:
                if node_id not in graph:
                    graph.add_node(
                        node_id,
                        label=label,
                        node_type="entity",
                        document_id=document_id,
                        frequency=1,
                        version_id=version_id,
                        confidence=relation.get("confidence", 0.5),
                    )
            graph.add_edge(
                source_id,
                target_id,
                relation=relation.get("relation", "RELATED_TO"),
                source_chunk_id=chunk_id,
                confidence=relation.get("confidence", 0.5),
                char_start=relation.get("char_start", -1),
                char_end=relation.get("char_end", -1),
                version_id=version_id,
            )

    def _add_shallow_relations(
        self, graph: nx.DiGraph, keyword_ids: list[str], chunk_id: str
    ) -> None:
        for left, right in zip(keyword_ids, keyword_ids[1:]):
            if graph.has_edge(left, right):
                graph[left][right]["weight"] += 1
            else:
                graph.add_edge(
                    left,
                    right,
                    relation="CO_OCCURS",
                    weight=1,
                    source_chunk_id=chunk_id,
                )

    def _add_deep_relations(
        self, graph: nx.DiGraph, keyword_ids: list[str], chunk_id: str
    ) -> None:
        for index, left in enumerate(keyword_ids):
            for right in keyword_ids[index + 1 :]:
                if graph.has_edge(left, right):
                    graph[left][right]["weight"] = graph[left][right].get("weight", 1) + 1
                    continue
                graph.add_edge(
                    left,
                    right,
                    relation="SEMANTICALLY_LINKED",
                    weight=1,
                    source_chunk_id=chunk_id,
                    confidence=0.5,
                )

    def _keywords(self, text: str, limit: int = 12) -> Dict[str, int]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
        stopwords = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "from",
            "this",
            "have",
            "your",
            "into",
            "was",
            "were",
            "are",
            "not",
            "but",
            "you",
            "can",
            "all",
        }
        filtered = [token for token in tokens if token not in stopwords]
        return dict(Counter(filtered).most_common(limit))

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        return slug or "entity"
