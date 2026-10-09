from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class GroundedExtraction:
    label: str
    value: Any
    char_start: int
    char_end: int
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class GroundingVerifier:
    """Verifies extracted values against source text with exact offsets where possible."""

    def verify(self, source_text: str, extractions: List[Dict[str, Any]]) -> List[GroundedExtraction]:
        verified: list[GroundedExtraction] = []
        lower_source = source_text.lower()
        for extraction in extractions:
            label = str(extraction.get("label", "value"))
            value = extraction.get("value", "")
            value_text = str(value)
            index = lower_source.find(value_text.lower())
            if index < 0:
                verified.append(
                    GroundedExtraction(
                        label=label,
                        value=value,
                        char_start=-1,
                        char_end=-1,
                        confidence=0.0,
                        metadata={"verified": False, "reason": "value_not_found"},
                    )
                )
                continue
            verified.append(
                GroundedExtraction(
                    label=label,
                    value=value,
                    char_start=index,
                    char_end=index + len(value_text),
                    confidence=1.0,
                    metadata={"verified": True},
                )
            )
        return verified
