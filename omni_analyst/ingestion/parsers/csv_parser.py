from __future__ import annotations

import csv
import io

from omni_analyst.ingestion.models import (
    DocumentSource,
    LayoutBlock,
    PageLayout,
    ParsedDocument,
    ParsedElement,
    TableCell,
    TableRecord,
)
from omni_analyst.ingestion.parsers.base import BaseParser


class CsvParser(BaseParser):
    parser_name = "csv"
    media_types = ("text/csv", "application/csv")
    extensions = (".csv",)

    def parse(self, source: DocumentSource) -> ParsedDocument:
        decoded = source.read_bytes().decode("utf-8", errors="ignore")
        reader = csv.reader(io.StringIO(decoded))
        rows = list(reader)
        row_text = [" | ".join(row) for row in rows]
        text = "\n".join(row_text)
        table = TableRecord(
            table_id=f"{source.document_id}-csv-table",
            rows=[
                [
                    TableCell(
                        row_index=row_index,
                        column_index=column_index,
                        text=value,
                        metadata={"source": "csv"},
                    )
                    for column_index, value in enumerate(row)
                ]
                for row_index, row in enumerate(rows)
            ],
            source="csv",
        )
        return ParsedDocument(
            document_id=source.document_id,
            filename=source.filename,
            media_type=source.media_type or "text/csv",
            parser_name=self.parser_name,
            text=text,
            elements=[
                ParsedElement(
                    element_type="table_row",
                    text=line,
                    metadata={"row_index": idx},
                )
                for idx, line in enumerate(row_text)
            ],
            pages=[
                PageLayout(
                    page_number=1,
                    text=text,
                    blocks=[
                        LayoutBlock(
                            block_id=f"{source.document_id}-row-{idx}",
                            block_type="table_row",
                            text=line,
                            page_number=1,
                            metadata={"row_index": idx},
                        )
                        for idx, line in enumerate(row_text)
                    ],
                    tables=[table] if rows else [],
                )
            ],
            tables=[table] if rows else [],
            metadata={
                "rows": len(rows),
                "columns": max((len(row) for row in rows), default=0),
                "table_records": 1 if rows else 0,
            },
        )
