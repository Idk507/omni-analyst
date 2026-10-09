from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Any, Dict, List

from omni_analyst.cache.semantic_cache import SemanticCache
from omni_analyst.hooks.runtime import HookRuntime
from omni_analyst.models.contracts import HookEvent


class WebSearchIntelligenceLayer:
    """Free web search layer using DDGS when available."""

    def __init__(
        self,
        *,
        semantic_cache: SemanticCache | None = None,
        hook_runtime: HookRuntime | None = None,
        retries: int = 1,
        timeout_seconds: int = 10,
    ) -> None:
        self.semantic_cache = semantic_cache
        self.hook_runtime = hook_runtime
        self.retries = retries
        self.timeout_seconds = timeout_seconds

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        use_cache: bool = True,
        namespace: str = "wsil",
    ) -> Dict[str, Any]:
        normalized_query = self._normalize_query(query)
        if use_cache and self.semantic_cache is not None:
            cached = self.semantic_cache.get(normalized_query, namespace=namespace)
            if cached:
                return {**cached, "provider": cached.get("provider", "ddgs-cache")}

        if self.hook_runtime is not None:
            pre_records = self.hook_runtime.fire_detailed(
                HookEvent.PRE_TOOL_USE,
                {"tool_name": "web_search", "query": normalized_query},
            )
            blocked = next((record for record in pre_records if not record.allowed), None)
            if blocked:
                return self._diagnostic(
                    query,
                    "blocked",
                    blocked.decision.reason or "Blocked by search policy hook",
                    hook_records=[record.model_dump(mode="json") for record in pre_records],
                )

        results: list[dict[str, Any]] = []
        provider = "ddgs"
        diagnostics: list[dict[str, Any]] = []
        for attempt in range(self.retries + 1):
            try:
                from ddgs import DDGS

                with DDGS(timeout=self.timeout_seconds) as ddgs:
                    for item in ddgs.text(normalized_query, max_results=max_results * 2):
                        results.append(self._normalize_result(item))
                break
            except Exception as exc:
                diagnostics.append(
                    {"attempt": attempt + 1, "error": str(exc), "provider": "ddgs"}
                )
                provider = "unavailable"

        results = self._rank(self._dedupe(results))[:max_results]
        if not results and diagnostics:
            results.append(
                {
                    "title": "DDGS unavailable",
                    "url": "",
                    "snippet": diagnostics[-1]["error"],
                    "source_type": "diagnostic",
                    "domain": "",
                    "score": 0.0,
                }
            )
        response = {
            "provider": provider,
            "query": query,
            "normalized_query": normalized_query,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "diagnostics": diagnostics,
            "citations": [
                {
                    "citation_id": f"cite-{index}",
                    "url": result["url"],
                    "title": result["title"],
                    "evidence_spans": [result["snippet"]] if result["snippet"] else [],
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }
                for index, result in enumerate(results)
            ],
            "results": results,
        }
        if self.semantic_cache is not None and provider == "ddgs":
            self.semantic_cache.put(
                f"wsil:{normalized_query}",
                normalized_query,
                response,
                namespace=namespace,
                metadata={"provider": "ddgs"},
                ttl_seconds=3600,
            )
        if self.hook_runtime is not None:
            records = self.hook_runtime.fire_detailed(
                HookEvent.POST_TOOL_USE,
                {"tool_name": "web_search", "query": normalized_query, "result": response},
            )
            response["hook_records"] = [record.model_dump(mode="json") for record in records]
        return response

    def _normalize_query(self, query: str) -> str:
        return " ".join(query.strip().split())

    def _normalize_result(self, item: Dict[str, Any]) -> Dict[str, Any]:
        url = item.get("href", "") or item.get("url", "")
        return {
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("body", "") or item.get("snippet", ""),
            "source_type": "web",
            "domain": urlparse(url).netloc.lower(),
            "score": 0.0,
        }

    def _dedupe(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for result in results:
            key = result.get("url") or f"{result.get('title')}:{result.get('snippet')}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(result)
        return deduped

    def _rank(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for index, result in enumerate(results):
            score = 1.0 / (index + 1)
            if result.get("domain"):
                score += 0.1
            if result.get("snippet"):
                score += 0.1
            result["score"] = round(score, 4)
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _diagnostic(
        self,
        query: str,
        provider: str,
        message: str,
        *,
        hook_records: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        return {
            "provider": provider,
            "query": query,
            "normalized_query": self._normalize_query(query),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "diagnostics": [{"error": message}],
            "hook_records": hook_records or [],
            "citations": [],
            "results": [
                {
                    "title": "Search blocked",
                    "url": "",
                    "snippet": message,
                    "source_type": "diagnostic",
                    "domain": "",
                    "score": 0.0,
                }
            ],
        }
