from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List

from omni_analyst.ingestion.models import DocumentSource, OCRSpan


@dataclass
class OCRResult:
    engine: str
    text: str
    confidence: float
    spans: List[OCRSpan] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseOCREngine(ABC):
    engine_name = "base"

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def extract(self, source: DocumentSource) -> OCRResult:
        raise NotImplementedError
