from __future__ import annotations

import fitz

from omni_analyst.ingestion.models import (
    BoundingBox,
    DocumentSource,
    LayoutBlock,
    OCRSpan,
    PageLayout,
    ParsedDocument,
    ParsedElement,
    TableCell,
    TableRecord,
)
from omni_analyst.ingestion.parsers.base import BaseParser


class PdfParser(BaseParser):
    parser_name = "pdf"
    media_types = ("application/pdf",)
    extensions = (".pdf",)

    def __init__(
        self,
        *,
        ocr_language: str = "eng",
        ocr_dpi: int = 300,
        min_native_text_chars: int = 20,
    ) -> None:
        self.ocr_language = ocr_language
        self.ocr_dpi = ocr_dpi
        self.min_native_text_chars = min_native_text_chars

    def parse(self, source: DocumentSource) -> ParsedDocument:
        document = fitz.open(stream=source.read_bytes(), filetype="pdf")
        elements: list[ParsedElement] = []
        pages: list[PageLayout] = []
        tables: list[TableRecord] = []
        ocr_spans: list[OCRSpan] = []
        page_texts: list[str] = []
        ocr_pages = 0
        ocr_errors: list[dict[str, str | int]] = []

        try:
            for page_index, page in enumerate(document):
                text = page.get_text("text").strip()
                extraction_method = "native_text"
                blocks = self._layout_blocks(source.document_id, page, page_index + 1)
                page_tables = self._tables(source.document_id, page, page_index + 1)
                if len(text) < self.min_native_text_chars:
                    ocr_text = self._ocr_page(page, page_index, ocr_errors)
                    if ocr_text.strip():
                        text = ocr_text.strip()
                        extraction_method = "pymupdf_tesseract_ocr"
                        ocr_pages += 1
                        ocr_spans.append(
                            OCRSpan(
                                text=text,
                                engine="pymupdf_tesseract_ocr",
                                confidence=0.75,
                                page_number=page_index + 1,
                                metadata={"source": "pdf_page_ocr"},
                            )
                        )

                if not text:
                    continue

                page_texts.append(text)
                tables.extend(page_tables)
                pages.append(
                    PageLayout(
                        page_number=page_index + 1,
                        text=text,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        blocks=blocks
                        or [
                            LayoutBlock(
                                block_id=f"{source.document_id}-p{page_index + 1}-text",
                                block_type="text",
                                text=text,
                                page_number=page_index + 1,
                                confidence=1.0 if extraction_method == "native_text" else 0.75,
                            )
                        ],
                        tables=page_tables,
                        ocr_spans=[
                            span for span in ocr_spans if span.page_number == page_index + 1
                        ],
                        metadata={"extraction_method": extraction_method},
                    )
                )
                elements.append(
                    ParsedElement(
                        element_type="page_text",
                        text=text,
                        metadata={
                            "page_index": page_index,
                            "page_number": page_index + 1,
                            "extraction_method": extraction_method,
                        },
                    )
                )
        finally:
            document.close()

        full_text = "\n\n".join(page_texts).strip()
        return ParsedDocument(
            document_id=source.document_id,
            filename=source.filename,
            media_type=source.media_type or "application/pdf",
            parser_name=self.parser_name,
            text=full_text,
            elements=elements,
            pages=pages,
            tables=tables,
            ocr_spans=ocr_spans,
            metadata={
                "has_text_layer": any(
                    element.metadata.get("extraction_method") == "native_text"
                    for element in elements
                ),
                "needs_ocr": bool(ocr_errors) or ocr_pages > 0,
                "ocr_pages": ocr_pages,
                "ocr_errors": ocr_errors,
                "pages_detected": len(page_texts),
                "layout_blocks": sum(len(page.blocks) for page in pages),
                "tables": len(tables),
                "ocr_span_count": len(ocr_spans),
            },
        )

    def _ocr_page(
        self,
        page: fitz.Page,
        page_index: int,
        errors: list[dict[str, str | int]],
    ) -> str:
        try:
            text_page = page.get_textpage_ocr(
                language=self.ocr_language,
                dpi=self.ocr_dpi,
                full=True,
            )
            return page.get_text("text", textpage=text_page)
        except Exception as exc:
            errors.append({"page_index": page_index, "error": str(exc)})
            return ""

    def _layout_blocks(
        self,
        document_id: str,
        page: fitz.Page,
        page_number: int,
    ) -> list[LayoutBlock]:
        blocks: list[LayoutBlock] = []
        try:
            payload = page.get_text("dict")
        except Exception:
            return blocks
        for index, block in enumerate(payload.get("blocks", [])):
            bbox = block.get("bbox")
            lines: list[str] = []
            for line in block.get("lines", []):
                line_text = "".join(span.get("text", "") for span in line.get("spans", []))
                if line_text.strip():
                    lines.append(line_text.strip())
            text = "\n".join(lines).strip()
            if not text:
                continue
            blocks.append(
                LayoutBlock(
                    block_id=f"{document_id}-p{page_number}-b{index}",
                    block_type="text",
                    text=text,
                    page_number=page_number,
                    bbox=BoundingBox(
                        x0=float(bbox[0]),
                        y0=float(bbox[1]),
                        x1=float(bbox[2]),
                        y1=float(bbox[3]),
                        page_number=page_number,
                    )
                    if bbox
                    else None,
                    metadata={"source": "pymupdf_dict"},
                )
            )
        return blocks

    def _tables(
        self,
        document_id: str,
        page: fitz.Page,
        page_number: int,
    ) -> list[TableRecord]:
        tables: list[TableRecord] = []
        try:
            found = page.find_tables()
        except Exception:
            return tables
        for table_index, table in enumerate(getattr(found, "tables", []) or []):
            rows: list[list[TableCell]] = []
            try:
                extracted = table.extract()
            except Exception:
                continue
            for row_index, row in enumerate(extracted):
                cells: list[TableCell] = []
                for column_index, value in enumerate(row):
                    cells.append(
                        TableCell(
                            row_index=row_index,
                            column_index=column_index,
                            text="" if value is None else str(value).strip(),
                            metadata={"source": "pymupdf_find_tables"},
                        )
                    )
                rows.append(cells)
            if rows:
                tables.append(
                    TableRecord(
                        table_id=f"{document_id}-p{page_number}-table-{table_index}",
                        rows=rows,
                        page_number=page_number,
                        source="pdf",
                    )
                )
        return tables
