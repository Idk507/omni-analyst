from __future__ import annotations

import time
import re
from collections import defaultdict, deque
from typing import Any, Dict, Protocol

from omni_analyst.models.contracts import MiddlewarePhase, MiddlewareRecord


class Middleware(Protocol):
    name: str

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ...


class MiddlewareStack:
    """Ordered LangChain-style lifecycle middleware stack."""

    def __init__(self) -> None:
        self._middleware: list[Middleware] = []
        self._records: list[MiddlewareRecord] = []

    def add(self, middleware: Middleware) -> None:
        self._middleware.append(middleware)

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # Backward-compatible alias used by existing tests.
        return self.run_phase(MiddlewarePhase.BEFORE_AGENT, state)

    def run_phase(
        self, phase: MiddlewarePhase | str, state: Dict[str, Any]
    ) -> Dict[str, Any]:
        current = dict(state)
        fired: list[str] = []
        for middleware in self._middleware:
            current = self._apply_middleware(middleware, phase, current)
            fired.append(middleware.name)
        current.setdefault("middleware_fired", [])
        current["middleware_fired"] = list(current["middleware_fired"]) + fired
        return current

    def before_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.run_phase(MiddlewarePhase.BEFORE_AGENT, state)

    def before_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.run_phase(MiddlewarePhase.BEFORE_MODEL, state)

    def after_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.run_phase(MiddlewarePhase.AFTER_MODEL, state)

    def after_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.run_phase(MiddlewarePhase.AFTER_AGENT, state)

    def wrap_tool_call(self, state: Dict[str, Any], call) -> Any:
        wrapped_state = self.run_phase(MiddlewarePhase.WRAP_TOOL_CALL, state)
        return call(wrapped_state)

    def wrap_model_call(self, state: Dict[str, Any], call) -> Any:
        wrapped_state = self.run_phase(MiddlewarePhase.WRAP_MODEL_CALL, state)
        return call(wrapped_state)

    def records(self) -> list[MiddlewareRecord]:
        return list(self._records)

    def _apply_middleware(
        self, middleware: Middleware, phase: MiddlewarePhase | str, state: Dict[str, Any]
    ) -> Dict[str, Any]:
        phase_name = phase.value if isinstance(phase, MiddlewarePhase) else phase
        method = getattr(middleware, phase_name, None) or getattr(middleware, "apply", None)
        if method is None:
            return state
        started = time.perf_counter()
        current = method(dict(state))
        updates = {
            key: value for key, value in current.items() if state.get(key) != value
        }
        self._records.append(
            MiddlewareRecord(
                name=middleware.name,
                phase=phase_name,
                allowed=not current.get("blocked", False),
                state_updates=updates,
                warnings=list(current.get("warnings", [])),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )
        return current


class PIIMiddleware:
    name = "pii"

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        text = str(state.get("query", ""))
        state["pii_detected"] = "@" in text or any(char.isdigit() for char in text)
        if state["pii_detected"]:
            state.setdefault("warnings", []).append("Potential PII detected")
        redacted = re.sub(r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", text)
        redacted = re.sub(r"\b(?:\d[ -]?){12,19}\b", "[REDACTED_NUMBER]", redacted)
        if redacted != text:
            state["redacted_query"] = redacted
            state.setdefault("redactions", []).append({"type": "pii", "field": "query"})
        return state

    def before_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class ToolCallLimitMiddleware:
    name = "tool_call_limit"

    def __init__(self, limit: int = 20) -> None:
        self.limit = limit

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state["tool_call_limit"] = self.limit
        current = int(state.get("tool_call_count", 0))
        if current > self.limit:
            state["blocked"] = True
            state.setdefault("warnings", []).append("Tool call limit exceeded")
        return state

    def before_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)

    def wrap_tool_call(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class RetrievalGroundingMiddleware:
    name = "retrieval_grounding"

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state["grounding_required"] = True
        return state

    def before_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)

    def after_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class SkillsMiddleware:
    name = "skills"

    def __init__(self, max_skills: int = 3) -> None:
        self.max_skills = max_skills

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        available = state.get("available_skills") or []
        query = str(state.get("query") or state.get("prompt") or "").lower()
        matched: list[dict[str, Any]] = []
        for skill in available:
            content = f"{skill.get('name', '')} {skill.get('description', '')} {' '.join(skill.get('triggers', []))}".lower()
            score = len(set(re.findall(r"[a-z0-9]+", query)) & set(re.findall(r"[a-z0-9]+", content)))
            if score > 0:
                matched.append({"skill": skill, "score": score})
        matched.sort(key=lambda item: item["score"], reverse=True)
        state["selected_skills"] = [item["skill"] for item in matched[: self.max_skills]]
        if state["selected_skills"]:
            state.setdefault("context_injections", []).append(
                {
                    "type": "skills",
                    "count": len(state["selected_skills"]),
                }
            )
        return state

    def before_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)

    def before_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class GraphContextInjector:
    name = "graph_context"

    def __init__(self, max_facts: int = 3) -> None:
        self.max_facts = max_facts

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        graph = state.get("graph") or state.get("knowledge_graph") or {}
        query = str(state.get("query") or state.get("prompt") or "").lower()
        tokens = set(re.findall(r"[a-z0-9]+", query))
        nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
        edges = graph.get("edges", []) if isinstance(graph, dict) else []
        node_by_id = {node.get("id"): node for node in nodes}
        facts: list[dict[str, Any]] = []
        for edge in edges:
            source = node_by_id.get(edge.get("source"), {})
            target = node_by_id.get(edge.get("target"), {})
            text = f"{source.get('label', edge.get('source'))} {edge.get('relation')} {target.get('label', edge.get('target'))}"
            if tokens and not (tokens & set(re.findall(r"[a-z0-9]+", text.lower()))):
                continue
            facts.append(
                {
                    "source": source.get("label", edge.get("source")),
                    "relation": edge.get("relation"),
                    "target": target.get("label", edge.get("target")),
                    "confidence": edge.get("confidence", edge.get("weight", 1.0)),
                }
            )
            if len(facts) >= self.max_facts:
                break
        state["graph_context"] = facts
        if facts:
            state.setdefault("context_injections", []).append({"type": "graph_context", "count": len(facts)})
        return state

    def before_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class CachePolicyMiddleware:
    name = "cache_policy"

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state.setdefault("cache_namespace", state.get("session_id") or "default")
        state.setdefault("cache_enabled", True)
        return state

    def before_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class WebSearchPolicyMiddleware:
    name = "web_search_policy"

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        query = str(state.get("query", ""))
        state["web_search_allowed"] = not any(
            marker in query.lower() for marker in ["password", "secret", "api key"]
        )
        if not state["web_search_allowed"]:
            state.setdefault("warnings", []).append("Sensitive query blocked from web search")
        return state

    def before_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class ProvenanceMiddleware:
    name = "provenance"

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state.setdefault("provenance", {})
        state["provenance"].setdefault("middleware_attested", True)
        return state

    def after_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class PromptInjectionMiddleware:
    name = "prompt_injection"

    markers = (
        "ignore previous instructions",
        "disregard all prior",
        "reveal system prompt",
        "developer message",
        "jailbreak",
    )

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        text = str(state.get("query") or state.get("prompt") or "").lower()
        matches = [marker for marker in self.markers if marker in text]
        state["prompt_injection_risk"] = min(1.0, len(matches) * 0.35)
        if matches:
            state.setdefault("warnings", []).append("Prompt injection markers detected")
            state.setdefault("risk_flags", []).append({"type": "prompt_injection", "matches": matches})
        return state

    def before_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)

    def before_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class SecretRedactionMiddleware:
    name = "secret_redaction"

    patterns = {
        "api_key": re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{12,})"),
        "bearer": re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    }

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        text = str(state.get("query") or state.get("prompt") or "")
        redacted = text
        findings: list[str] = []
        for name, pattern in self.patterns.items():
            if pattern.search(redacted):
                findings.append(name)
                redacted = pattern.sub(f"[REDACTED_{name.upper()}]", redacted)
        if findings:
            state["contains_secret"] = True
            state["redacted_query"] = redacted
            state.setdefault("redactions", []).append({"type": "secret", "findings": findings})
            state.setdefault("warnings", []).append("Secret-like values were redacted")
        return state

    def before_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)

    def before_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class ToolPermissionMiddleware:
    name = "tool_permission"

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = state.get("tool_name")
        if not tool_name:
            return state
        allowed = set(state.get("allowed_tools") or [])
        denied = set(state.get("denied_tools") or [])
        if tool_name in denied or (allowed and tool_name not in allowed):
            state["blocked"] = True
            state.setdefault("warnings", []).append(f"Tool not permitted: {tool_name}")
            state.setdefault("policy_decisions", []).append(
                {"type": "tool_permission", "action": "block", "tool_name": tool_name}
            )
        return state

    def wrap_tool_call(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class ModelBudgetMiddleware:
    name = "model_budget"

    def __init__(self, max_tokens: int = 8192, max_cost_units: float = 10.0) -> None:
        self.max_tokens = max_tokens
        self.max_cost_units = max_cost_units

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        requested = int(state.get("max_tokens", 0) or 0)
        spent = float(state.get("cost_units", 0.0) or 0.0)
        if requested > self.max_tokens:
            state["max_tokens"] = self.max_tokens
            state.setdefault("warnings", []).append("Model token request capped by middleware")
        if spent > self.max_cost_units:
            state["blocked"] = True
            state.setdefault("warnings", []).append("Model budget exceeded")
        return state

    def before_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class RetrievalCitationMiddleware:
    name = "retrieval_citation"

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not state.get("grounding_required"):
            return state
        response = str(state.get("response", ""))
        citations = state.get("citations") or state.get("sources") or []
        if response and not citations:
            state.setdefault("warnings", []).append("Grounded response is missing citations")
            state.setdefault("risk_flags", []).append({"type": "missing_citations"})
        return state

    def after_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class OutputSchemaMiddleware:
    name = "output_schema"

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        schema = state.get("output_schema")
        payload = state.get("output")
        if not isinstance(schema, dict) or payload is None:
            return state
        missing = [key for key in schema.get("required", []) if key not in payload]
        state["schema_valid"] = not missing
        if missing:
            state.setdefault("warnings", []).append(f"Output missing required fields: {', '.join(missing)}")
        return state

    def after_model(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class RateLimitMiddleware:
    name = "rate_limit"

    def __init__(self, limit: int = 60, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        key = str(state.get("user_id") or state.get("session_id") or "anonymous")
        now = time.time()
        events = self._events[key]
        while events and now - events[0] > self.window_seconds:
            events.popleft()
        events.append(now)
        state["rate_limit_remaining"] = max(0, self.limit - len(events))
        if len(events) > self.limit:
            state["blocked"] = True
            state.setdefault("warnings", []).append("Rate limit exceeded")
        return state

    def before_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


class AuditMiddleware:
    name = "audit"

    def apply(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state.setdefault("audit_events", []).append(
            {
                "phase": state.get("phase", "unknown"),
                "session_id": state.get("session_id"),
                "blocked": bool(state.get("blocked", False)),
                "warnings": list(state.get("warnings", [])),
            }
        )
        return state

    def after_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.apply(state)


def build_default_middleware_stack() -> MiddlewareStack:
    stack = MiddlewareStack()
    stack.add(PromptInjectionMiddleware())
    stack.add(SecretRedactionMiddleware())
    stack.add(PIIMiddleware())
    stack.add(RateLimitMiddleware())
    stack.add(ToolCallLimitMiddleware())
    stack.add(ToolPermissionMiddleware())
    stack.add(ModelBudgetMiddleware())
    stack.add(SkillsMiddleware())
    stack.add(RetrievalGroundingMiddleware())
    stack.add(GraphContextInjector())
    stack.add(RetrievalCitationMiddleware())
    stack.add(OutputSchemaMiddleware())
    stack.add(CachePolicyMiddleware())
    stack.add(WebSearchPolicyMiddleware())
    stack.add(ProvenanceMiddleware())
    stack.add(AuditMiddleware())
    return stack
