from __future__ import annotations

from abc import ABC, abstractmethod

from omni_analyst.ingestion.models import DocumentSource, ParsedDocument


class BaseParser(ABC):
    media_types: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    parser_name = "base"

    def matches(self, source: DocumentSource) -> bool:
        media_type = (source.media_type or "").lower()
        return source.extension in self.extensions or media_type in self.media_types

    @abstractmethod
    def parse(self, source: DocumentSource) -> ParsedDocument:
        raise NotImplementedError
