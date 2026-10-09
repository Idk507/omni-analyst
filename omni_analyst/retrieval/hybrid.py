from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from omni_analyst.ingestion.models import ChunkRecord


@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: str
    text: str
    score: float
    scores: Dict[str, float] = field(default_factory=dict)
    metadata: Dict = field(default_factory=dict)


class FaissVectorIndex:
    """FAISS-backed vector index with deterministic in-memory fallback."""

    def __init__(self, dimensions: int = 128) -> None:
        self.dimensions = dimensions
        self.ids: list[str] = []
        self.vectors: dict[str, list[float]] = {}
        self.backend = "fallback"
        self._index: Any | None = None
        self._np: Any | None = None
        try:
            import faiss  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return
        self.backend = "faiss"
        self._np = np
        self._index = faiss.IndexFlatIP(dimensions)

    def add(self, chunk_id: str, vector: list[float]) -> None:
        normalized = self._normalize(vector)
        self.ids.append(chunk_id)
        self.vectors[chunk_id] = normalized
        if self._index is not None and self._np is not None:
            array = self._np.array([normalized], dtype="float32")
            self._index.add(array)

    def search(self, query_vector: list[float], *, top_k: int) -> Dict[str, float]:
        normalized = self._normalize(query_vector)
        if self._index is not None and self._np is not None and self.ids:
            scores, indices = self._index.search(self._np.array([normalized], dtype="float32"), min(top_k, len(self.ids)))
            return {
                self.ids[int(index)]: float(score)
                for score, index in zip(scores[0], indices[0])
                if int(index) >= 0 and float(score) > 0
            }
        scored = [(chunk_id, self._dot(normalized, vector)) for chunk_id, vector in self.vectors.items()]
        scored.sort(key=lambda item: item[1], reverse=True)
        return {chunk_id: score for chunk_id, score in scored[:top_k] if score > 0}

    def stats(self) -> Dict[str, Any]:
        return {"backend": self.backend, "dimensions": self.dimensions, "vectors": len(self.ids)}

    def _normalize(self, vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def _dot(self, left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right))


class ColBERTReranker:
    """Deterministic ColBERT-style late interaction reranker."""

    def __init__(self, embedder) -> None:
        self.embedder = embedder

    def score(self, query_tokens: list[str], doc_token_vectors: list[list[float]]) -> float:
        if not query_tokens or not doc_token_vectors:
            return 0.0
        query_vectors = [self.embedder(token) for token in query_tokens]
        max_sims = [
            max(self._cosine(query_vector, doc_vector) for doc_vector in doc_token_vectors)
            for query_vector in query_vectors
        ]
        return sum(max_sims) / len(max_sims)

    def rerank(self, query: str, results: List[RetrievalResult], token_vectors: Dict[str, list[list[float]]], *, top_k: int = 5) -> List[RetrievalResult]:
        query_tokens = re.findall(r"[a-z0-9]+", query.lower())
        reranked: list[RetrievalResult] = []
        for result in results:
            colbert = self.score(query_tokens, token_vectors.get(result.chunk_id, []))
            result.scores["colbert_late_interaction"] = colbert
            result.score += colbert
            reranked.append(result)
        reranked.sort(key=lambda item: item.score, reverse=True)
        return reranked[:top_k]

    def _cosine(self, left: list[float], right: list[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return numerator / (left_norm * right_norm)


class HybridRetriever:
    """Hybrid retriever with FAISS-compatible dense search and ColBERT-style rerank."""

    def __init__(self, embedding_dimensions: int = 128) -> None:
        self._chunks: Dict[str, ChunkRecord] = {}
        self._doc_freq: Counter[str] = Counter()
        self._term_freq: Dict[str, Counter[str]] = {}
        self._vectors: Dict[str, list[float]] = {}
        self._token_vectors: Dict[str, list[list[float]]] = {}
        self.embedding_dimensions = embedding_dimensions
        self.vector_index = FaissVectorIndex(embedding_dimensions)
        self.colbert = ColBERTReranker(self._embed)
        self._avg_len = 0.0

    def index(self, chunks: Iterable[ChunkRecord]) -> None:
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self._term_freq = {}
        self._doc_freq = Counter()
        self.vector_index = FaissVectorIndex(self.embedding_dimensions)
        lengths = []
        for chunk in self._chunks.values():
            tokens = self._tokens(chunk.text)
            lengths.append(len(tokens))
            counts = Counter(tokens)
            self._term_freq[chunk.chunk_id] = counts
            self._vectors[chunk.chunk_id] = self._embed(chunk.text)
            self._token_vectors[chunk.chunk_id] = [self._embed(token) for token in tokens]
            self.vector_index.add(chunk.chunk_id, self._vectors[chunk.chunk_id])
            for token in counts:
                self._doc_freq[token] += 1
        self._avg_len = sum(lengths) / len(lengths) if lengths else 0.0

    def search(self, query: str, *, top_k: int = 5) -> List[RetrievalResult]:
        query_tokens = self._tokens(query)
        vector_candidates = self._faiss_search(query, top_k=max(top_k * 4, 20))
        results: list[RetrievalResult] = []
        for chunk_id, chunk in self._chunks.items():
            bm25 = self._bm25_score(query_tokens, chunk_id)
            vector = vector_candidates.get(chunk_id, 0.0)
            colbert = self.colbert.score(query_tokens, self._token_vectors.get(chunk_id, []))
            score = 0.45 * bm25 + 0.30 * vector + 0.25 * colbert
            if score <= 0:
                continue
            results.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    score=score,
                    scores={
                        "bm25": bm25,
                        "faiss_vector": vector,
                        "colbert_late_interaction": colbert,
                    },
                    metadata=chunk.metadata,
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def _bm25_score(self, query_tokens: list[str], chunk_id: str) -> float:
        counts = self._term_freq.get(chunk_id, Counter())
        doc_len = sum(counts.values()) or 1
        total_docs = max(len(self._chunks), 1)
        k1 = 1.5
        b = 0.75
        score = 0.0
        for token in query_tokens:
            tf = counts[token]
            if tf == 0:
                continue
            df = self._doc_freq[token]
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1 - b + b * doc_len / max(self._avg_len, 1))
            score += idf * (tf * (k1 + 1)) / denom
        return score

    def _faiss_search(self, query: str, *, top_k: int) -> Dict[str, float]:
        query_vector = self._embed(query)
        return self.vector_index.search(query_vector, top_k=top_k)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.embedding_dimensions
        for token in self._tokens(text):
            vector[hash(token) % self.embedding_dimensions] += 1.0
        return vector

    def _cosine(self, left: list[float], right: list[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return numerator / (left_norm * right_norm)

    def _tokens(self, text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def stats(self) -> Dict[str, Any]:
        return {
            "chunks": len(self._chunks),
            "vector_index": self.vector_index.stats(),
            "reranker": "colbert_late_interaction",
        }


class ReciprocalRankFusionReranker:
    def rerank(self, result_sets: List[List[RetrievalResult]], *, top_k: int = 5) -> List[RetrievalResult]:
        fused: dict[str, RetrievalResult] = {}
        scores: defaultdict[str, float] = defaultdict(float)
        for results in result_sets:
            for rank, result in enumerate(results, start=1):
                fused[result.chunk_id] = result
                scores[result.chunk_id] += 1.0 / (60 + rank)
        ranked = list(fused.values())
        for item in ranked:
            item.scores["rrf"] = scores[item.chunk_id]
            item.score += scores[item.chunk_id]
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:top_k]
