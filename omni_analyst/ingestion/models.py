from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class DocumentSource:
    document_id: str
    filename: str
    media_type: str | None = None
    path: str | None = None
    content_base64: str | None = None
    text: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def extension(self) -> str:
        return Path(self.filename).suffix.lower()

    def read_bytes(self) -> bytes:
        if self.content_base64:
            return base64.b64decode(self.content_base64)
        if self.path:
            return Path(self.path).read_bytes()
        if self.text is not None:
            return self.text.encode("utf-8")
        return b""


@dataclass
class ParsedElement:
    element_type: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    page_number: Optional[int] = None


@dataclass
class OCRSpan:
    text: str
    engine: str
    confidence: float = 0.0
    page_number: Optional[int] = None
    bbox: Optional[BoundingBox] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LayoutBlock:
    block_id: str
    block_type: str
    text: str = ""
    page_number: Optional[int] = None
    bbox: Optional[BoundingBox] = None
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TableCell:
    row_index: int
    column_index: int
    text: str
    bbox: Optional[BoundingBox] = None
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TableRecord:
    table_id: str
    rows: List[List[TableCell]]
    page_number: Optional[int] = None
    source: str = ""
    bbox: Optional[BoundingBox] = None
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_text(self) -> str:
        return "\n".join(
            " | ".join(cell.text for cell in row)
            for row in self.rows
        )


@dataclass
class PageLayout:
    page_number: int
    text: str = ""
    width: Optional[float] = None
    height: Optional[float] = None
    blocks: List[LayoutBlock] = field(default_factory=list)
    tables: List[TableRecord] = field(default_factory=list)
    ocr_spans: List[OCRSpan] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    document_id: str
    filename: str
    media_type: str
    parser_name: str
    text: str
    elements: List[ParsedElement] = field(default_factory=list)
    pages: List[PageLayout] = field(default_factory=list)
    tables: List[TableRecord] = field(default_factory=list)
    ocr_spans: List[OCRSpan] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    quality_score: float = 0.0


@dataclass
class IngestionArtifact:
    document: ParsedDocument
    strategy: str
    chunks: List[ChunkRecord]
    metadata: Dict[str, Any] = field(default_factory=dict)
