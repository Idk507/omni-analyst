from __future__ import annotations

from omni_analyst.ingestion.models import DocumentSource
from omni_analyst.ingestion.ocr.ocr_orchestrator import OCROrchestrator
from omni_analyst.ingestion.parsers.base import BaseParser
from omni_analyst.ingestion.parsers.csv_parser import CsvParser
from omni_analyst.ingestion.parsers.docx_parser import DocxParser
from omni_analyst.ingestion.parsers.image_parser import ImageParser
from omni_analyst.ingestion.parsers.pdf_parser import PdfParser
from omni_analyst.ingestion.parsers.pptx_parser import PptxParser
from omni_analyst.ingestion.parsers.text_parser import TextParser
from omni_analyst.ingestion.parsers.xlsx_parser import XlsxParser


class FileRouter:
    def __init__(self, ocr_orchestrator: OCROrchestrator | None = None) -> None:
        self.ocr_orchestrator = ocr_orchestrator or OCROrchestrator()
        self.parsers: list[BaseParser] = [
            PdfParser(),
            DocxParser(),
            CsvParser(),
            XlsxParser(),
            PptxParser(),
            ImageParser(self.ocr_orchestrator),
            TextParser(),
        ]

    def get_parser(self, source: DocumentSource) -> BaseParser:
        for parser in self.parsers:
            if parser.matches(source):
                return parser
        raise ValueError(f"Unsupported file type for {source.filename}")
