from __future__ import annotations

import re
from typing import Any, Callable, Dict, List

from omni_analyst.extraction.grounding import GroundingVerifier
from omni_analyst.extraction.schema_registry import ExtractionSchema, SchemaRegistry


class LangExtractRunner:
    """Optional LangExtract adapter with deterministic local fallback.

    The real `langextract` package requires model/provider configuration for LLM-backed
    extraction. This runner exposes the integration boundary while keeping tests and
    offline execution deterministic.
    """

    def __init__(
        self,
        schema_registry: SchemaRegistry | None = None,
        grounding_verifier: GroundingVerifier | None = None,
        provider: Callable[[str, ExtractionSchema], List[Dict[str, Any]]] | None = None,
    ) -> None:
        self.schema_registry = schema_registry or SchemaRegistry()
        self.grounding_verifier = grounding_verifier or GroundingVerifier()
        self.provider = provider

    def extract(
        self,
        text: str,
        *,
        schema_id: str = "generic_document_v1",
        use_provider: bool = False,
    ) -> Dict[str, Any]:
        schema = self.schema_registry.get(schema_id)
        raw_extractions = self._provider_extract(text, schema) if use_provider else []
        if not raw_extractions:
            raw_extractions = self._fallback_extract(text, schema)
        grounded = self.grounding_verifier.verify(text, raw_extractions)
        relations = self._relations(text, [item.__dict__ for item in grounded])
        return {
            "schema_id": schema.schema_id,
            "provider": "langextract" if use_provider and self.provider else "local_fallback",
            "entities": [item.__dict__ for item in grounded],
            "relations": relations,
            "source_spans": [
                {
                    "label": item.label,
                    "value": item.value,
                    "char_start": item.char_start,
                    "char_end": item.char_end,
                    "confidence": item.confidence,
                }
                for item in grounded
            ],
            "validation": {
                "schema_valid": True,
                "grounded_count": sum(1 for item in grounded if item.metadata.get("verified")),
                "ungrounded_count": sum(1 for item in grounded if not item.metadata.get("verified")),
            },
        }

    def _provider_extract(
        self, text: str, schema: ExtractionSchema
    ) -> List[Dict[str, Any]]:
        try:
            import langextract as lx  # noqa: F401
        except Exception:
            return []
        if self.provider is None:
            return []
        return self.provider(text, schema)

    def _fallback_extract(
        self, text: str, schema: ExtractionSchema
    ) -> List[Dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for match in re.finditer(r"\b[A-Z][A-Za-z0-9&.-]{2,}(?:\s+[A-Z][A-Za-z0-9&.-]{2,}){0,3}\b", text):
            candidates.append({"label": "entity", "value": match.group(0)})
        for match in re.finditer(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", text):
            candidates.append({"label": "date", "value": match.group(0)})
        for match in re.finditer(r"(?:\$|USD\s*)?\d+(?:,\d{3})*(?:\.\d+)?%?", text):
            candidates.append({"label": "amount", "value": match.group(0)})
        if text.strip():
            candidates.append({"label": "summary", "value": text.strip()[:160]})
        return candidates[:50]

    def _relations(self, text: str, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        relation_patterns = [
            (r"\b(?P<left>[A-Z][A-Za-z0-9&.-]{2,})\s+(?:paid|pays)\s+(?P<right>(?:USD\s*)?\d+(?:,\d{3})*(?:\.\d+)?)", "PAID"),
            (r"\b(?P<left>[A-Z][A-Za-z0-9&.-]{2,})\s+(?:acquired|bought)\s+(?P<right>[A-Z][A-Za-z0-9&.-]{2,})", "ACQUIRED"),
            (r"\b(?P<left>[A-Z][A-Za-z0-9&.-]{2,})\s+(?:signed|approved)\s+(?P<right>[A-Z][A-Za-z0-9&.-]{2,})", "SIGNED"),
        ]
        relations: list[dict[str, Any]] = []
        for pattern, relation in relation_patterns:
            for match in re.finditer(pattern, text):
                relations.append(
                    {
                        "source": match.group("left"),
                        "target": match.group("right"),
                        "relation": relation,
                        "char_start": match.start(),
                        "char_end": match.end(),
                        "confidence": 0.85,
                    }
                )
        entity_values = [str(item.get("value")) for item in entities if item.get("label") == "entity"]
        for left, right in zip(entity_values, entity_values[1:]):
            if left != right and not any(r["source"] == left and r["target"] == right for r in relations):
                relations.append(
                    {
                        "source": left,
                        "target": right,
                        "relation": "MENTIONS_WITH",
                        "confidence": 0.55,
                    }
                )
        return relations[:50]
