from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

import api.main as api_main
from omni_analyst.audit.store import AuditStore
from omni_analyst.hitl.store import HitlStore
from omni_analyst.mcp.client import MCPStreamableHttpClient
from omni_analyst.mcp.interceptors.registry import MCPInterceptorRegistry
from omni_analyst.mcp.models import MCPServerConfig, MCPToolDescriptor
from omni_analyst.mcp.registry import MCPRegistry
from omni_analyst.memory.fabric import MemoryFabric
from omni_analyst.models.contracts import ResultRoute, RiskTier, TaskContract, TaskPriority
from omni_analyst.orchestrator.result_bus import TaskResultBus
from omni_analyst.orchestrator.service import OrchestratorService
from omni_analyst.policy.engine import PolicyEngine
from omni_analyst.runtime.service import RuntimeService
from omni_analyst.scheduler.task_scheduler import TaskScheduler


class RuntimeCoreTests(unittest.TestCase):
    def test_scheduler_releases_dependent_task_after_completion(self) -> None:
        scheduler = TaskScheduler()
        root = TaskContract(task_type="SEARCH", created_by="orch")
        child = TaskContract(
            task_type="ANALYSIS",
            created_by="orch",
            dependencies=[root.task_id],
        )

        scheduler.add_task(root)
        scheduler.add_task(child)

        ready = scheduler.next_ready(limit=5)
        self.assertEqual([task.task_id for task in ready], [root.task_id])

        scheduler.mark_done(root.task_id)
        ready_after = scheduler.next_ready(limit=5)
        self.assertEqual([task.task_id for task in ready_after], [child.task_id])

    def test_scheduler_leases_and_recovers_expired_worker_tasks(self) -> None:
        scheduler = TaskScheduler()
        task = TaskContract(task_type="SEARCH", created_by="worker-test")
        scheduler.add_task(task)

        leased = scheduler.lease_ready("worker-1", limit=1, lease_seconds=1)
        self.assertEqual(leased[0].output["worker"]["worker_id"], "worker-1")

        recovered = scheduler.recover_expired_leases(
            now=datetime.now(timezone.utc) + timedelta(seconds=2)
        )
        self.assertEqual(recovered, 1)
        self.assertEqual(scheduler.get_task(task.task_id).status, "QUEUED")

    def test_orchestrator_routes_task_results_to_result_bus(self) -> None:
        scheduler = TaskScheduler()
        result_bus = TaskResultBus()
        orchestrator = OrchestratorService(
            scheduler=scheduler,
            policy_engine=PolicyEngine(),
            audit_store=AuditStore(),
            hitl_store=HitlStore(),
            result_bus=result_bus,
        )
        task = TaskContract(
            task_type="GRAPH_BUILD",
            created_by="orch",
            result_routes=[
                ResultRoute.MEMORY,
                ResultRoute.GRAPH,
                ResultRoute.CONSENSUS,
                ResultRoute.STREAM,
            ],
        )
        orchestrator.submit_task(task)
        orchestrator.dispatch_ready()
        completed = orchestrator.complete_task(task.task_id, {"graph": {"nodes": []}})

        self.assertEqual(completed.status, "DONE")
        self.assertEqual(result_bus.snapshot()["graph_events"], 1)
        self.assertEqual(result_bus.snapshot()["stream_events"], 1)
        self.assertGreaterEqual(len(completed.output["result_bus_receipts"]), 4)

    def test_r3_task_creates_hitl_request_and_pauses(self) -> None:
        scheduler = TaskScheduler()
        audit_store = AuditStore()
        hitl_store = HitlStore()
        orchestrator = OrchestratorService(
            scheduler=scheduler,
            policy_engine=PolicyEngine(),
            audit_store=audit_store,
            hitl_store=hitl_store,
        )

        task = TaskContract(
            task_type="DELETE",
            created_by="orch",
            risk_tier=RiskTier.R3,
            priority=TaskPriority.CRITICAL,
            session_id="sess-1",
        )

        result = orchestrator.submit_task(task)

        self.assertTrue(result.hitl_required)
        self.assertEqual(result.status.value, "PAUSED")
        self.assertEqual(len(hitl_store.list_requests()), 1)
        self.assertGreaterEqual(len(audit_store.list_events(session_id="sess-1")), 2)

    def test_runtime_service_builds_document_dag(self) -> None:
        scheduler = TaskScheduler()
        orchestrator = OrchestratorService(
            scheduler=scheduler,
            policy_engine=PolicyEngine(),
            audit_store=AuditStore(),
            hitl_store=HitlStore(),
        )
        runtime = RuntimeService(
            orchestrator=orchestrator,
            memory_fabric=MemoryFabric(),
        )
        planned = runtime.plan_query(
            query="extract obligations",
            session_id="sess-2",
            metadata={"has_document": True},
        )

        self.assertEqual(
            [task.task_type for task in planned],
            ["EXTRACTION", "GRAPH_BUILD", "AUDIT", "ANALYSIS"],
        )
        self.assertEqual(planned[1].dependencies, [planned[0].task_id])


class ApiSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "result": {
                            "provider": "mock_mcp",
                            "query": body["arguments"]["query"],
                            "hits": [
                                {
                                    "title": "GDPR obligations",
                                    "url": "https://example.test/gdpr",
                                }
                            ],
                        }
                    }
                ),
            )

        mock_mcp_registry = MCPRegistry()
        mock_mcp_registry.register_server(
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
                    )
                ],
            )
        )

        api_main.registry = api_main.AgentRegistry()
        api_main.scheduler = api_main.TaskScheduler()
        api_main.policy_engine = api_main.PolicyEngine()
        api_main.audit_store = api_main.AuditStore()
        api_main.hitl_store = api_main.HitlStore()
        api_main.memory_fabric = api_main.MemoryFabric()
        api_main.mcp_registry = mock_mcp_registry
        api_main.mcp_interceptor_registry = MCPInterceptorRegistry(
            audit_store=api_main.audit_store,
            base_state={"runtime": "test"},
        )
        api_main.mcp_client = MCPStreamableHttpClient(
            registry=api_main.mcp_registry,
            interceptor_registry=api_main.mcp_interceptor_registry,
            transport=httpx.MockTransport(handler),
        )
        api_main.orchestrator = api_main.OrchestratorService(
            scheduler=api_main.scheduler,
            policy_engine=api_main.policy_engine,
            audit_store=api_main.audit_store,
            hitl_store=api_main.hitl_store,
        )
        api_main.runtime = api_main.RuntimeService(
            orchestrator=api_main.orchestrator,
            memory_fabric=api_main.memory_fabric,
            mcp_client=api_main.mcp_client,
        )
        self.client = TestClient(api_main.app)

    def test_query_endpoint_creates_tasks(self) -> None:
        response = self.client.post(
            "/api/v5/query",
            json={"query": "summarize gdpr", "session_id": "sess-api", "metadata": {}},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertGreaterEqual(len(payload["planned_tasks"]), 1)
        self.assertEqual(len(payload["mcp_results"]), 1)
        self.assertEqual(payload["failed_tasks"], [])
        self.assertEqual(
            payload["mcp_results"][0]["result"]["provider"],
            "mock_mcp",
        )

    def test_operator_run_returns_dynamic_trace(self) -> None:
        response = self.client.post(
            "/api/v5/operator/runs",
            json={
                "query": "explain runtime trace",
                "provider_id": "local_echo",
                "include_graph_tool": False,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "DONE")
        kinds = {event["kind"] for event in payload["timeline"]}
        self.assertIn("thinking", kinds)
        self.assertIn("middleware", kinds)
        self.assertIn("hooks", kinds)
        self.assertIn("model", kinds)
        self.assertTrue(payload["answer"])

    def test_memory_endpoint_reflects_query(self) -> None:
        self.client.post(
            "/api/v5/query",
            json={"query": "summarize gdpr", "session_id": "sess-mem", "metadata": {}},
        )
        response = self.client.get("/api/v5/memory/sess-mem")
        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["short_term"][0]["type"], "user_query")

    def test_worker_state_and_ingestion_diagnostics_endpoints(self) -> None:
        diagnostics = self.client.post(
            "/api/v5/ingestion/diagnostics",
            json={
                "source": {
                    "document_id": "diag-doc",
                    "filename": "diag.txt",
                    "text": "Diagnostics document for ingestion.",
                }
            },
        )
        self.assertEqual(diagnostics.status_code, 200)
        self.assertEqual(diagnostics.json()["document"]["document_id"], "diag-doc")

        worker_state = self.client.get("/api/v5/workers/state")
        self.assertEqual(worker_state.status_code, 200)
        self.assertIn("cache_backend", worker_state.json()["environment"])

    def test_upload_and_query_executes_document_pipeline(self) -> None:
        response = self.client.post(
            "/api/v5/upload-and-query",
            json={
                "query": "extract revenue rows",
                "session_id": "sess-doc",
                "metadata": {},
                "uploaded_files": [
                    {
                        "document_id": "doc-csv-1",
                        "filename": "revenue.csv",
                        "media_type": "text/csv",
                        "text": "quarter,revenue\nQ1,120\nQ2,140",
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["failed_tasks"], [])
        completed_types = [task["task_type"] for task in payload["completed_tasks"]]
        self.assertIn("EXTRACTION", completed_types)
        self.assertIn("GRAPH_BUILD", completed_types)
        extraction_task = next(
            task for task in payload["completed_tasks"] if task["task_type"] == "EXTRACTION"
        )
        self.assertEqual(extraction_task["output"]["documents"][0]["parser_name"], "csv")
        self.assertGreaterEqual(extraction_task["output"]["documents"][0]["chunk_count"], 1)


if __name__ == "__main__":
    unittest.main()
