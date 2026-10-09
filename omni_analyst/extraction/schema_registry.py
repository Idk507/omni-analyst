from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, List


@dataclass
class ExtractionField:
    name: str
    description: str
    field_type: str = "string"
    required: bool = False


@dataclass
class ExtractionSchema:
    schema_id: str
    description: str
    fields: List[ExtractionField] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)


class SchemaRegistry:
    def __init__(self) -> None:
        self._schemas: Dict[str, ExtractionSchema] = {}
        self._lock = RLock()
        self.register(
            ExtractionSchema(
                schema_id="generic_document_v1",
                description="Generic document facts with grounded source spans.",
                fields=[
                    ExtractionField("entities", "Named entities or important concepts", "list"),
                    ExtractionField("dates", "Dates mentioned in the document", "list"),
                    ExtractionField("amounts", "Amounts or metrics mentioned in the document", "list"),
                    ExtractionField("summary", "Short grounded summary", "string"),
                ],
            )
        )

    def register(self, schema: ExtractionSchema) -> ExtractionSchema:
        with self._lock:
            self._schemas[schema.schema_id] = schema
            return schema

    def get(self, schema_id: str) -> ExtractionSchema:
        with self._lock:
            if schema_id not in self._schemas:
                raise KeyError(f"Unknown extraction schema: {schema_id}")
            return self._schemas[schema_id]

    def list_schemas(self) -> List[ExtractionSchema]:
        with self._lock:
            return list(self._schemas.values())
