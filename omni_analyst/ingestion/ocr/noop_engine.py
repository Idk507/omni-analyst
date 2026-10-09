from __future__ import annotations

from omni_analyst.ingestion.models import DocumentSource
from omni_analyst.ingestion.ocr.base import BaseOCREngine, OCRResult


class NoOpOCREngine(BaseOCREngine):
    engine_name = "noop"

    def is_available(self) -> bool:
        return True

    def extract(self, source: DocumentSource) -> OCRResult:
        return OCRResult(
            engine=self.engine_name,
            text="",
            confidence=0.0,
            metadata={
                "reason": f"No OCR engine could process {source.filename}",
            },
        )
