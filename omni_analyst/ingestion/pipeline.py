from __future__ import annotations

from typing import Iterable, List

from omni_analyst.ingestion.chunking import AntiChunkingEngine, ChunkQualityMiddleware
from omni_analyst.ingestion.file_router import FileRouter
from omni_analyst.ingestion.models import DocumentSource, IngestionArtifact


class IngestionPipeline:
    def __init__(
        self,
        file_router: FileRouter | None = None,
        anti_chunking: AntiChunkingEngine | None = None,
        chunk_quality: ChunkQualityMiddleware | None = None,
    ) -> None:
        self.file_router = file_router or FileRouter()
        self.anti_chunking = anti_chunking or AntiChunkingEngine()
        self.chunk_quality = chunk_quality or ChunkQualityMiddleware()

    def ingest(self, source: DocumentSource) -> IngestionArtifact:
        parser = self.file_router.get_parser(source)
        document = parser.parse(source)

        # PDF routing prefers native extraction first and marks OCR need explicitly.
        if source.extension == ".pdf" and not document.text.strip():
            document.metadata["needs_ocr"] = True

        strategy, chunks = self.anti_chunking.chunk(document)
        checked_chunks = self.chunk_quality.apply(document, chunks)
        return IngestionArtifact(
            document=document,
            strategy=strategy,
            chunks=checked_chunks,
            metadata={
                "chunk_count": len(checked_chunks),
                "page_count": len(document.pages),
                "table_count": len(document.tables),
                "ocr_span_count": len(document.ocr_spans),
                "layout_block_count": sum(len(page.blocks) for page in document.pages),
                "average_chunk_quality": (
                    sum(chunk.quality_score for chunk in checked_chunks) / len(checked_chunks)
                    if checked_chunks
                    else 0.0
                ),
            },
        )

    def ingest_many(self, sources: Iterable[DocumentSource]) -> List[IngestionArtifact]:
        return [self.ingest(source) for source in sources]
