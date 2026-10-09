from __future__ import annotations

from typing import Callable

from omni_analyst.ingestion.models import DocumentSource
from omni_analyst.ingestion.ocr.base import BaseOCREngine, OCRResult


class VisionOCREngine(BaseOCREngine):
    """Pluggable vision OCR adapter.

    A callable can be injected by deployments that use Azure Vision, Gemini,
    GPT vision, or another OCR-capable model. When not configured, the engine is
    unavailable and the orchestrator moves to the next engine with diagnostics.
    """

    engine_name = "vision"

    def __init__(self, extractor: Callable[[DocumentSource], OCRResult] | None = None) -> None:
        self.extractor = extractor

    def is_available(self) -> bool:
        return self.extractor is not None

    def extract(self, source: DocumentSource) -> OCRResult:
        if self.extractor is None:
            raise RuntimeError("vision OCR adapter is not configured")
        return self.extractor(source)
