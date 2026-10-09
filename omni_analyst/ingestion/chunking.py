from __future__ import annotations

import re
from itertools import count
from typing import Iterable, List

from omni_analyst.ingestion.models import ChunkRecord, ParsedDocument


class AntiChunkingEngine:
    """Selects chunk boundaries that preserve paragraphs and section flow."""

    def select_strategy(self, document: ParsedDocument) -> str:
        if document.tables or document.metadata.get("rows") or document.metadata.get("slides"):
            return "structural"
        if len(document.text) > 5000:
            return "parent_document"
        return "semantic"

    def chunk(self, document: ParsedDocument) -> tuple[str, List[ChunkRecord]]:
        strategy = self.select_strategy(document)
        if strategy == "structural":
            chunks = self._chunk_structural(document)
        elif strategy == "parent_document":
            chunks = self._chunk_parent_document(document)
        else:
            chunks = self._chunk_semantic(document)
        return strategy, chunks

    def _chunk_structural(self, document: ParsedDocument) -> List[ChunkRecord]:
        records: list[ChunkRecord] = []
        for table_index, table in enumerate(document.tables):
            table_text = table.as_text().strip()
            if not table_text:
                continue
            records.append(
                ChunkRecord(
                    chunk_id=f"{document.document_id}-table-{table_index}",
                    document_id=document.document_id,
                    text=table_text,
                    metadata={
                        "strategy": "structural",
                        "element_type": "table",
                        "table_id": table.table_id,
                        "page_number": table.page_number,
                        "source": table.source,
                        "confidence": table.confidence,
                    },
                )
            )
        for index, element in enumerate(document.elements):
            text = element.text.strip()
            if not text:
                continue
            records.append(
                ChunkRecord(
                    chunk_id=f"{document.document_id}-chunk-{index}",
                    document_id=document.document_id,
                    text=text,
                    metadata={
                        "strategy": "structural",
                        "element_type": element.element_type,
                        **element.metadata,
                    },
                )
            )
        return records or self._chunk_semantic(document)

    def _chunk_parent_document(self, document: ParsedDocument) -> List[ChunkRecord]:
        paragraphs = [part.strip() for part in document.text.split("\n\n") if part.strip()]
        return self._group_chunks(
            document=document,
            parts=paragraphs,
            strategy="parent_document",
            max_chars=1200,
        )

    def _chunk_semantic(self, document: ParsedDocument) -> List[ChunkRecord]:
        paragraphs = [part.strip() for part in document.text.split("\n\n") if part.strip()]
        if not paragraphs:
            paragraphs = [part.strip() for part in re.split(r"(?<=[.!?])\s+", document.text) if part.strip()]
        return self._group_chunks(
            document=document,
            parts=paragraphs,
            strategy="semantic",
            max_chars=800,
        )

    def _group_chunks(
        self,
        *,
        document: ParsedDocument,
        parts: Iterable[str],
        strategy: str,
        max_chars: int,
    ) -> List[ChunkRecord]:
        chunks: list[ChunkRecord] = []
        chunk_index = count()
        current: list[str] = []
        current_size = 0

        for part in parts:
            if not part:
                continue
            projected = current_size + len(part) + (2 if current else 0)
            if current and projected > max_chars:
                chunks.append(self._build_chunk(document, strategy, next(chunk_index), current))
                current = [part]
                current_size = len(part)
            else:
                current.append(part)
                current_size = projected

        if current:
            chunks.append(self._build_chunk(document, strategy, next(chunk_index), current))
        return chunks

    def _build_chunk(
        self,
        document: ParsedDocument,
        strategy: str,
        index: int,
        parts: List[str],
    ) -> ChunkRecord:
        text = "\n\n".join(parts).strip()
        return ChunkRecord(
            chunk_id=f"{document.document_id}-chunk-{index}",
            document_id=document.document_id,
            text=text,
            metadata={
                "strategy": strategy,
                "char_count": len(text),
                "page_numbers": [
                    page.page_number
                    for page in document.pages
                    if page.text and any(part in page.text for part in parts[:2])
                ],
                "source_elements": len(parts),
                "average_ocr_confidence": self._average_ocr_confidence(document),
            },
        )

    def _average_ocr_confidence(self, document: ParsedDocument) -> float:
        if not document.ocr_spans:
            return 1.0
        return round(sum(span.confidence for span in document.ocr_spans) / len(document.ocr_spans), 4)


class ChunkQualityMiddleware:
    """Scores chunks and re-chunks low-quality results with safer boundaries."""

    def __init__(self, threshold: float = 0.55) -> None:
        self.threshold = threshold

    def apply(
        self,
        document: ParsedDocument,
        chunks: List[ChunkRecord],
    ) -> List[ChunkRecord]:
        rescored = [self._score_chunk(chunk) for chunk in chunks]
        low_quality = [chunk for chunk in rescored if chunk.quality_score < self.threshold]
        if not low_quality or len(rescored) <= 1:
            return rescored

        refined: list[ChunkRecord] = []
        for chunk in rescored:
            if chunk.quality_score >= self.threshold:
                refined.append(chunk)
                continue
            refined.extend(self._split_chunk(document.document_id, chunk))
        return [self._score_chunk(chunk) for chunk in refined]

    def _split_chunk(self, document_id: str, chunk: ChunkRecord) -> List[ChunkRecord]:
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", chunk.text)
            if sentence.strip()
        ]
        if len(sentences) <= 1:
            return [chunk]
        refined: list[ChunkRecord] = []
        for index, sentence in enumerate(sentences):
            refined.append(
                ChunkRecord(
                    chunk_id=f"{chunk.chunk_id}-retry-{index}",
                    document_id=document_id,
                    text=sentence,
                    metadata={**chunk.metadata, "rechunked": True},
                )
            )
        return refined

    def _score_chunk(self, chunk: ChunkRecord) -> ChunkRecord:
        text = chunk.text.strip()
        score = 0.0
        if 120 <= len(text) <= 1200:
            score += 0.35
        elif len(text) >= 40:
            score += 0.2
        if text.endswith((".", "!", "?", ":", ";")):
            score += 0.25
        if not text[:1].islower():
            score += 0.15
        if "\n\n" in text or len(re.findall(r"(?<=[.!?])\s+", text)) >= 1:
            score += 0.15
        confidence = chunk.metadata.get("confidence", chunk.metadata.get("average_ocr_confidence"))
        if isinstance(confidence, (int, float)):
            score *= max(0.2, min(float(confidence), 1.0))
        chunk.quality_score = min(score, 1.0)
        return chunk
