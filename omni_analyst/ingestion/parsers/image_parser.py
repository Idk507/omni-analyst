from __future__ import annotations

from omni_analyst.ingestion.models import DocumentSource, LayoutBlock, PageLayout, ParsedDocument, ParsedElement
from omni_analyst.ingestion.ocr.ocr_orchestrator import OCROrchestrator
from omni_analyst.ingestion.parsers.base import BaseParser


class ImageParser(BaseParser):
    parser_name = "image"
    media_types = ("image/png", "image/jpeg", "image/jpg", "image/webp")
    extensions = (".png", ".jpg", ".jpeg", ".webp")

    def __init__(self, ocr_orchestrator: OCROrchestrator | None = None) -> None:
        self.ocr_orchestrator = ocr_orchestrator or OCROrchestrator()

    def parse(self, source: DocumentSource) -> ParsedDocument:
        ocr_result = self.ocr_orchestrator.extract(source)
        text = ocr_result.text.strip()
        return ParsedDocument(
            document_id=source.document_id,
            filename=source.filename,
            media_type=source.media_type or "image/*",
            parser_name=f"{self.parser_name}:{ocr_result.engine}",
            text=text,
            elements=[
                ParsedElement(
                    element_type="ocr_text",
                    text=text,
                    metadata={
                        "ocr_engine": ocr_result.engine,
                        "confidence": ocr_result.confidence,
                    },
                )
            ]
            if text
            else [],
            pages=[
                PageLayout(
                    page_number=1,
                    text=text,
                    blocks=[
                        LayoutBlock(
                            block_id=f"{source.document_id}-image-ocr",
                            block_type="ocr_text",
                            text=text,
                            page_number=1,
                            confidence=ocr_result.confidence,
                            metadata={"ocr_engine": ocr_result.engine},
                        )
                    ]
                    if text
                    else [],
                    ocr_spans=ocr_result.spans,
                    metadata={"source": "image"},
                )
            ],
            ocr_spans=ocr_result.spans,
            metadata={
                "ocr_engine": ocr_result.engine,
                "ocr_confidence": ocr_result.confidence,
                "ocr_span_count": len(ocr_result.spans),
                **ocr_result.metadata,
            },
        )
