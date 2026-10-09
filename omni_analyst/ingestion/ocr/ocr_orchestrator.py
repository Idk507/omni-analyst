from __future__ import annotations

from typing import Iterable, List

from omni_analyst.ingestion.models import DocumentSource
from omni_analyst.ingestion.ocr.base import BaseOCREngine, OCRResult
from omni_analyst.ingestion.ocr.noop_engine import NoOpOCREngine
from omni_analyst.ingestion.ocr.tesseract_engine import TesseractOCREngine
from omni_analyst.ingestion.ocr.vision_engine import VisionOCREngine


class OCROrchestrator:
    """Chooses the first healthy OCR engine that can yield text."""

    def __init__(self, engines: Iterable[BaseOCREngine] | None = None) -> None:
        configured = list(engines or [VisionOCREngine(), TesseractOCREngine(), NoOpOCREngine()])
        self.engines: List[BaseOCREngine] = configured

    def extract(self, source: DocumentSource) -> OCRResult:
        errors: list[str] = []
        for engine in self.engines:
            if not engine.is_available():
                errors.append(f"{engine.engine_name}: unavailable")
                continue
            try:
                result = engine.extract(source)
            except Exception as exc:
                errors.append(f"{engine.engine_name}: {exc}")
                continue
            if result.text.strip():
                if errors:
                    result.metadata["fallback_errors"] = errors
                return result
            errors.append(
                f"{engine.engine_name}: empty result"
            )
        fallback = NoOpOCREngine().extract(source)
        fallback.metadata["fallback_errors"] = errors
        return fallback
