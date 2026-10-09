from __future__ import annotations

from omni_analyst.ingestion.models import DocumentSource, ParsedDocument, ParsedElement
from omni_analyst.ingestion.parsers.base import BaseParser


class TextParser(BaseParser):
    parser_name = "text"
    media_types = ("text/plain", "text/markdown")
    extensions = (".txt", ".md")

    def parse(self, source: DocumentSource) -> ParsedDocument:
        if source.text is not None:
            text = source.text
        else:
            text = source.read_bytes().decode("utf-8", errors="ignore")
        return ParsedDocument(
            document_id=source.document_id,
            filename=source.filename,
            media_type=source.media_type or "text/plain",
            parser_name=self.parser_name,
            text=text,
            elements=[ParsedElement(element_type="text", text=text)],
            metadata={"line_count": len(text.splitlines())},
        )
