from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from omni_analyst.extraction.langextract_runner import LangExtractRunner
from omni_analyst.graph.simple_builder import SimpleGraphBuilder
from omni_analyst.ingestion.models import DocumentSource, IngestionArtifact
from omni_analyst.ingestion.pipeline import IngestionPipeline


class LangExtractNetworkXPipeline:
    """End-to-end ingestion, grounded extraction, and NetworkX graph construction."""

    def __init__(
        self,
        ingestion_pipeline: IngestionPipeline | None = None,
        extractor: LangExtractRunner | None = None,
        graph_builder: SimpleGraphBuilder | None = None,
    ) -> None:
        self.ingestion_pipeline = ingestion_pipeline or IngestionPipeline()
        self.extractor = extractor or LangExtractRunner()
        self.graph_builder = graph_builder or SimpleGraphBuilder(extractor=self.extractor)
        self._artifacts: dict[str, IngestionArtifact] = {}
        self._graphs: dict[str, dict[str, Any]] = {}

    def ingest_sources(
        self,
        sources: Iterable[DocumentSource],
        *,
        depth: str = "deep",
        use_provider: bool = False,
    ) -> dict[str, Any]:
        artifacts: list[IngestionArtifact] = []
        for source in sources:
            artifact = self.ingestion_pipeline.ingest(source)
            extraction = self.extractor.extract(artifact.document.text, use_provider=use_provider)
            artifact.metadata["langextract"] = extraction
            artifact.metadata["graph_pipeline"] = {
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "schema_id": extraction.get("schema_id"),
                "provider": extraction.get("provider"),
                "grounded_count": extraction.get("validation", {}).get("grounded_count", 0),
                "relation_count": len(extraction.get("relations", [])),
            }
            self._artifacts[artifact.document.document_id] = artifact
            artifacts.append(artifact)

        graph = self.graph_builder.build(artifacts, depth=depth)
        self._graphs[graph["version_id"]] = graph
        return {
            "graph": graph,
            "artifacts": [self._artifact_summary(artifact) for artifact in artifacts],
            "pipeline": {
                "depth": depth,
                "artifact_count": len(artifacts),
                "use_provider": use_provider,
            },
        }

    def build_from_existing(self, *, document_ids: list[str] | None = None, depth: str = "deep") -> dict[str, Any]:
        artifacts = [
            artifact
            for document_id, artifact in self._artifacts.items()
            if document_ids is None or document_id in document_ids
        ]
        graph = self.graph_builder.build(artifacts, depth=depth)
        self._graphs[graph["version_id"]] = graph
        return {"graph": graph, "artifact_count": len(artifacts)}

    def graph(self, version_id: str | None = None) -> dict[str, Any]:
        if not self._graphs:
            return {"nodes": [], "edges": [], "metrics": {"node_count": 0, "edge_count": 0}}
        if version_id is None:
            version_id = next(reversed(self._graphs))
        return self._graphs[version_id]

    def lineage(self, document_id: str) -> dict[str, Any]:
        versions: list[dict[str, Any]] = []
        for version_id, graph in self._graphs.items():
            matching_nodes = [node for node in graph.get("nodes", []) if node.get("document_id") == document_id or node.get("id") == document_id]
            if matching_nodes:
                versions.append({"version_id": version_id, "node_count": len(matching_nodes)})
        return {"document_id": document_id, "versions": versions}

    def _artifact_summary(self, artifact: IngestionArtifact) -> dict[str, Any]:
        return {
            "document": asdict(artifact.document),
            "strategy": artifact.strategy,
            "chunk_count": len(artifact.chunks),
            "metadata": artifact.metadata,
        }
