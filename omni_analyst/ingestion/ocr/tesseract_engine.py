from __future__ import annotations

from io import BytesIO

import pytesseract
from PIL import Image

from omni_analyst.ingestion.models import BoundingBox, DocumentSource, OCRSpan
from omni_analyst.ingestion.ocr.base import BaseOCREngine, OCRResult


class TesseractOCREngine(BaseOCREngine):
    engine_name = "tesseract"

    def __init__(self, language: str = "eng") -> None:
        self.language = language

    def is_available(self) -> bool:
        try:
            pytesseract.get_tesseract_version()
        except Exception:
            return False
        return True

    def extract(self, source: DocumentSource) -> OCRResult:
        if not self.is_available():
            raise RuntimeError("tesseract executable is not available")

        image = Image.open(BytesIO(source.read_bytes()))
        text = pytesseract.image_to_string(image, lang=self.language)
        confidence, spans = self._confidence_spans(image)
        return OCRResult(
            engine=self.engine_name,
            text=text.strip(),
            confidence=confidence,
            spans=spans,
            metadata={"language": self.language},
        )

    def _average_confidence(self, image: Image.Image) -> float:
        confidence, _ = self._confidence_spans(image)
        return confidence

    def _confidence_spans(self, image: Image.Image) -> tuple[float, list[OCRSpan]]:
        try:
            data = pytesseract.image_to_data(
                image,
                lang=self.language,
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            return 0.75, []
        values: list[float] = []
        spans: list[OCRSpan] = []
        texts = data.get("text", [])
        confidences = data.get("conf", [])
        for index, raw_confidence in enumerate(confidences):
            if raw_confidence in ("", "-1"):
                continue
            value = float(raw_confidence)
            if value < 0:
                continue
            values.append(value)
            word = str(texts[index]).strip() if index < len(texts) else ""
            if not word:
                continue
            spans.append(
                OCRSpan(
                    text=word,
                    engine=self.engine_name,
                    confidence=round(value / 100.0, 4),
                    bbox=self._bbox(data, index),
                    metadata={"language": self.language},
                )
            )
        if not values:
            return 0.0, []
        return round(sum(values) / len(values) / 100.0, 4), spans

    def _bbox(self, data: dict[str, list], index: int) -> BoundingBox:
        left = float(data.get("left", [0])[index])
        top = float(data.get("top", [0])[index])
        width = float(data.get("width", [0])[index])
        height = float(data.get("height", [0])[index])
        return BoundingBox(x0=left, y0=top, x1=left + width, y1=top + height)
