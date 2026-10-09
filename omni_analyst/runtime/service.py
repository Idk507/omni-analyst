from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from omni_analyst.graph.simple_builder import SimpleGraphBuilder
from omni_analyst.ingestion.pipeline import IngestionPipeline
from omni_analyst.ingestion.models import (
    ChunkRecord,
    DocumentSource,
    IngestionArtifact,
    ParsedDocument,
)
from omni_analyst.memory.fabric import MemoryFabric
from omni_analyst.mcp.client import MCPStreamableHttpClient
from omni_analyst.models.contracts import RiskTier, TaskContract, TaskPriority
from omni_analyst.orchestrator.service import OrchestratorService
from omni_analyst.tool_gateway.service import ToolGateway


class RuntimeService:
    """Builds initial task graphs from query requests."""

    def __init__(
        self,
        orchestrator: OrchestratorService,
        memory_fabric: MemoryFabric,
        mcp_client: MCPStreamableHttpClient | None = None,
        ingestion_pipeline: IngestionPipeline | None = None,
        graph_builder: SimpleGraphBuilder | None = None,
        tool_gateway: ToolGateway | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.memory_fabric = memory_fabric
        self.mcp_client = mcp_client
        self.ingestion_pipeline = ingestion_pipeline or IngestionPipeline()
        self.graph_builder = graph_builder or SimpleGraphBuilder()
        self.tool_gateway = tool_gateway or ToolGateway(mcp_client=mcp_client)
        self.tool_gateway.register_alias("web_search", "mcp:web_search")
        self.tool_gateway.register_local_tool(
            "local:ingest_documents", self._local_ingest_documents
        )
        self.tool_gateway.register_local_tool(
            "local:build_document_graph", self._local_build_document_graph
        )

    def plan_query(
        self, *, query: str, session_id: str | None, metadata: Dict[str, Any]
    ) -> List[TaskContract]:
        trace_id = f"trace-{uuid4()}"
        has_document = bool(
            metadata.get("has_document")
            or metadata.get("document_ids")
            or metadata.get("uploaded_files")
        )

        if has_document:
            extraction = TaskContract(
                task_type="EXTRACTION",
                created_by="orchestrator",
                assigned_to="extraction_agent",
                priority=TaskPriority.HIGH,
                risk_tier=RiskTier.R0,
                input={"query": query, "metadata": metadata},
                session_id=session_id,
                trace_id=trace_id,
            )
            graph = TaskContract(
                task_type="GRAPH_BUILD",
                created_by="orchestrator",
                assigned_to="graph_builder_agent",
                priority=TaskPriority.NORMAL,
                risk_tier=RiskTier.R0,
                input={"query": query, "metadata": metadata},
                dependencies=[extraction.task_id],
                session_id=session_id,
                trace_id=trace_id,
            )
            audit = TaskContract(
                task_type="AUDIT",
                created_by="orchestrator",
                assigned_to="audit_agent",
                priority=TaskPriority.HIGH,
                risk_tier=RiskTier.R1,
                input={"query": query, "metadata": metadata},
                dependencies=[extraction.task_id],
                session_id=session_id,
                trace_id=trace_id,
            )
            summary = TaskContract(
                task_type="ANALYSIS",
                created_by="orchestrator",
                assigned_to="summary_agent",
                priority=TaskPriority.HIGH,
                risk_tier=RiskTier.R0,
                input={"query": query, "metadata": metadata},
                dependencies=[graph.task_id, audit.task_id],
                session_id=session_id,
                trace_id=trace_id,
            )
            return [extraction, graph, audit, summary]

        search = TaskContract(
            task_type="SEARCH",
            created_by="orchestrator",
            assigned_to="web_search_agent",
            priority=TaskPriority.HIGH,
            risk_tier=RiskTier.R1,
            input={"query": query, "metadata": metadata},
            session_id=session_id,
            trace_id=trace_id,
        )
        summary = TaskContract(
            task_type="ANALYSIS",
            created_by="orchestrator",
            assigned_to="summary_agent",
            priority=TaskPriority.HIGH,
            risk_tier=RiskTier.R0,
            input={"query": query, "metadata": metadata},
            dependencies=[search.task_id],
            session_id=session_id,
            trace_id=trace_id,
        )
        audit = TaskContract(
            task_type="AUDIT",
            created_by="orchestrator",
            assigned_to="audit_agent",
            priority=TaskPriority.NORMAL,
            risk_tier=RiskTier.R1,
            input={"query": query, "metadata": metadata},
            dependencies=[search.task_id],
            session_id=session_id,
            trace_id=trace_id,
        )
        return [search, summary, audit]

    def submit_query(
        self, *, query: str, session_id: str | None, metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        return asyncio.run(
            self.submit_query_async(
                query=query,
                session_id=session_id,
                metadata=metadata,
            )
        )

    async def submit_query_async(
        self, *, query: str, session_id: str | None, metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        tasks = self.plan_query(query=query, session_id=session_id, metadata=metadata)
        active_session_id = session_id or "session-unknown"
        self.memory_fabric.append_short_term(
            active_session_id,
            {"type": "user_query", "query": query, "metadata": metadata},
        )
        submitted = [self.orchestrator.submit_task(task) for task in tasks]
        initially_dispatched = self.orchestrator.dispatch_ready(limit=8)
        execution = await self._execute_ready_tasks(
            ready_tasks=initially_dispatched,
            session_id=active_session_id,
        )
        self.memory_fabric.append_episodic(
            active_session_id,
            {
                "type": "query_planned",
                "trace_id": tasks[0].trace_id if tasks else None,
                "task_ids": [str(task.task_id) for task in tasks],
            },
        )
        return {
            "accepted": True,
            "session_id": session_id,
            "trace_id": tasks[0].trace_id if tasks else None,
            "planned_tasks": submitted,
            "dispatched_tasks": execution["dispatched_tasks"],
            "completed_tasks": execution["completed_tasks"],
            "failed_tasks": execution["failed_tasks"],
            "mcp_results": execution["mcp_results"],
        }

    async def _execute_ready_tasks(
        self, *, ready_tasks: List[TaskContract], session_id: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        dispatched_tasks = [task.model_dump(mode="json") for task in ready_tasks]
        completed_tasks: List[Dict[str, Any]] = []
        failed_tasks: List[Dict[str, Any]] = []
        mcp_results: List[Dict[str, Any]] = []

        queue = list(ready_tasks)
        seen_running = {task.task_id for task in ready_tasks}
        search_result_payload: Dict[str, Any] = {}
        extraction_result_payload: Dict[str, Any] = {}
        graph_result_payload: Dict[str, Any] = {}

        while queue:
            task = queue.pop(0)
            if task.task_type == "SEARCH" and task.assigned_to == "web_search_agent":
                if self.tool_gateway is None:
                    failed = self.orchestrator.fail_task(
                        task.task_id,
                        "Tool gateway is not configured for web_search_agent",
                    )
                    failed_tasks.append(failed.model_dump(mode="json"))
                else:
                    try:
                        gateway_result = await self.tool_gateway.call(
                            "web_search",
                            arguments={"query": task.input["query"]},
                            session_id=task.session_id,
                            user_id=task.assigned_to,
                            trace_id=task.trace_id,
                            request_context={
                                "metadata": task.input.get("metadata", {}),
                                "task_id": str(task.task_id),
                            },
                        )
                        search_result_payload = {
                            "mcp_tool": gateway_result.tool_name,
                            "search_result": gateway_result.result,
                            "search_chunks": gateway_result.chunks,
                        }
                        completed = self.orchestrator.complete_task(
                            task.task_id,
                            output=search_result_payload,
                        )
                        completed_tasks.append(completed.model_dump(mode="json"))
                        mcp_results.append(
                            {
                                "tool_name": gateway_result.tool_name,
                                "provider": gateway_result.provider,
                                "result": gateway_result.result,
                                "chunks": gateway_result.chunks,
                            }
                        )
                        self.memory_fabric.append_episodic(
                            session_id,
                            {
                                "type": "mcp_web_search_completed",
                                "task_id": str(task.task_id),
                                "result": gateway_result.result,
                            },
                        )
                    except Exception as exc:  # pragma: no cover - protective runtime path
                        failed = self.orchestrator.fail_task(task.task_id, str(exc))
                        failed_tasks.append(failed.model_dump(mode="json"))
            elif task.task_type == "EXTRACTION" and task.assigned_to == "extraction_agent":
                try:
                    gateway_result = await self.tool_gateway.call(
                        "local:ingest_documents",
                        arguments={
                            "query": task.input["query"],
                            "metadata": task.input.get("metadata", {}),
                        },
                        session_id=task.session_id,
                        user_id=task.assigned_to,
                        trace_id=task.trace_id,
                    )
                    extraction_result_payload = gateway_result.result
                    completed = self.orchestrator.complete_task(
                        task.task_id,
                        output=extraction_result_payload,
                    )
                    completed_tasks.append(completed.model_dump(mode="json"))
                    for document in extraction_result_payload.get("documents", []):
                        document_id = document["document_id"]
                        self.memory_fabric.merge_document(
                            document_id,
                            {
                                "session_id": session_id,
                                "filename": document["filename"],
                                "parser": document["parser_name"],
                                "chunk_count": document["chunk_count"],
                            },
                        )
                    self.memory_fabric.append_episodic(
                        session_id,
                        {
                            "type": "document_extraction_completed",
                            "task_id": str(task.task_id),
                            "documents": extraction_result_payload.get("documents", []),
                        },
                    )
                except Exception as exc:
                    failed = self.orchestrator.fail_task(task.task_id, str(exc))
                    failed_tasks.append(failed.model_dump(mode="json"))
            elif task.task_type == "GRAPH_BUILD" and task.assigned_to == "graph_builder_agent":
                try:
                    gateway_result = await self.tool_gateway.call(
                        "local:build_document_graph",
                        arguments={
                            "ingestion_payload": extraction_result_payload,
                            "query": task.input["query"],
                        },
                        session_id=task.session_id,
                        user_id=task.assigned_to,
                        trace_id=task.trace_id,
                    )
                    graph_result_payload = gateway_result.result
                    completed = self.orchestrator.complete_task(
                        task.task_id,
                        output=graph_result_payload,
                    )
                    completed_tasks.append(completed.model_dump(mode="json"))
                except Exception as exc:
                    failed = self.orchestrator.fail_task(task.task_id, str(exc))
                    failed_tasks.append(failed.model_dump(mode="json"))
            elif task.task_type == "ANALYSIS" and task.assigned_to == "summary_agent":
                summary_source = (
                    extraction_result_payload
                    if extraction_result_payload
                    else search_result_payload.get("search_result", {})
                )
                completed = self.orchestrator.complete_task(
                    task.task_id,
                    output={
                        "summary": (
                            "Summary synthesized from document ingestion results."
                            if extraction_result_payload
                            else "Summary synthesized from MCP web search results."
                        ),
                        "source": summary_source,
                        "graph": graph_result_payload,
                    },
                )
                completed_tasks.append(completed.model_dump(mode="json"))
            elif task.task_type == "AUDIT" and task.assigned_to == "audit_agent":
                completed = self.orchestrator.complete_task(
                    task.task_id,
                    output={
                        "audit": "Read-only MCP web search path completed without policy violations.",
                        "risk_reviewed": True,
                    },
                )
                completed_tasks.append(completed.model_dump(mode="json"))

            new_ready = self.orchestrator.dispatch_ready(limit=8)
            for candidate in new_ready:
                if candidate.task_id in seen_running:
                    continue
                seen_running.add(candidate.task_id)
                queue.append(candidate)
                dispatched_tasks.append(candidate.model_dump(mode="json"))

        return {
            "dispatched_tasks": dispatched_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "mcp_results": mcp_results,
        }

    def _local_ingest_documents(
        self, *, query: str, metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        sources = self._document_sources_from_metadata(metadata)
        if not sources:
            raise ValueError(
                "No uploaded_files payload or resolvable document paths were provided."
            )

        artifacts = self.ingestion_pipeline.ingest_many(sources)
        return {
            "query": query,
            "documents": [self._artifact_summary(artifact) for artifact in artifacts],
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "text": chunk.text,
                    "metadata": chunk.metadata,
                    "quality_score": chunk.quality_score,
                }
                for artifact in artifacts
                for chunk in artifact.chunks
            ],
        }

    def _local_build_document_graph(
        self, *, ingestion_payload: Dict[str, Any], query: str
    ) -> Dict[str, Any]:
        artifacts = self._artifacts_from_payload(ingestion_payload)
        graph = self.graph_builder.build(artifacts)
        return {
            "query": query,
            "graph": graph,
            "document_count": len(artifacts),
        }

    def _artifact_summary(self, artifact: IngestionArtifact) -> Dict[str, Any]:
        return {
            "document_id": artifact.document.document_id,
            "filename": artifact.document.filename,
            "media_type": artifact.document.media_type,
            "parser_name": artifact.document.parser_name,
            "strategy": artifact.strategy,
            "text": artifact.document.text,
            "metadata": artifact.document.metadata,
            "chunk_count": len(artifact.chunks),
            "average_chunk_quality": artifact.metadata.get("average_chunk_quality", 0.0),
        }

    def _document_sources_from_metadata(
        self, metadata: Dict[str, Any]
    ) -> List[DocumentSource]:
        sources: list[DocumentSource] = []
        uploaded_files = metadata.get("uploaded_files", [])
        for index, payload in enumerate(uploaded_files):
            filename = payload.get("filename") or payload.get("path") or f"document-{index}"
            sources.append(
                DocumentSource(
                    document_id=payload.get("document_id") or f"doc-{index}",
                    filename=Path(filename).name,
                    media_type=payload.get("media_type"),
                    path=payload.get("path"),
                    content_base64=payload.get("content_base64"),
                    text=payload.get("text"),
                    metadata=payload.get("metadata", {}),
                )
            )

        if sources:
            return sources

        for index, value in enumerate(metadata.get("document_ids", [])):
            path = Path(value)
            if path.exists():
                sources.append(
                    DocumentSource(
                        document_id=f"doc-path-{index}",
                        filename=path.name,
                        path=str(path),
                    )
                )
        return sources

    def _artifacts_from_payload(
        self, payload: Dict[str, Any]
    ) -> List[IngestionArtifact]:
        artifacts: list[IngestionArtifact] = []
        chunks_by_doc: Dict[str, list[dict[str, Any]]] = {}
        for chunk in payload.get("chunks", []):
            chunks_by_doc.setdefault(chunk["document_id"], []).append(chunk)

        for document in payload.get("documents", []):
            document_id = document["document_id"]
            parsed = ParsedDocument(
                document_id=document_id,
                filename=document["filename"],
                media_type=document["media_type"],
                parser_name=document["parser_name"],
                text=document.get("text", ""),
                elements=[],
                metadata=document.get("metadata", {}),
            )
            artifact_chunks = []
            for chunk in chunks_by_doc.get(document_id, []):
                artifact_chunks.append(
                    ChunkRecord(
                        chunk_id=chunk["chunk_id"],
                        document_id=document_id,
                        text=chunk["text"],
                        metadata=chunk.get("metadata", {}),
                        quality_score=chunk.get("quality_score", 0.0),
                    )
                )
            artifacts.append(
                IngestionArtifact(
                    document=parsed,
                    strategy=document["strategy"],
                    chunks=artifact_chunks,
                    metadata={
                        "average_chunk_quality": document.get(
                            "average_chunk_quality", 0.0
                        )
                    },
                )
            )
        return artifacts

