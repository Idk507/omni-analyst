from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

from omni_analyst.models.contracts import SemanticCacheEntry, SemanticCacheLookup


@dataclass
class CacheEntry:
    key: str
    query: str
    value: Dict[str, Any]


class CacheEmbeddingStrategy:
    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            index = hash(token) % self.dimensions
            vector[index] += 1.0
        return vector


class SemanticCacheBackend(Protocol):
    backend_name: str

    def load(self) -> dict[str, SemanticCacheEntry]:
        ...

    def save(self, entries: dict[str, SemanticCacheEntry]) -> None:
        ...

    def stats(self) -> Dict[str, Any]:
        ...


class JsonSemanticCacheBackend:
    backend_name = "json"

    def __init__(self, storage_path: str | Path | None) -> None:
        self.storage_path = Path(storage_path) if storage_path else None

    def load(self) -> dict[str, SemanticCacheEntry]:
        entries: dict[str, SemanticCacheEntry] = {}
        if self.storage_path is None or not self.storage_path.exists():
            return entries
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        for item in payload.get("entries", []):
            entry = SemanticCacheEntry(**item)
            entries[f"{entry.namespace}:{entry.key}"] = entry
        return entries

    def save(self, entries: dict[str, SemanticCacheEntry]) -> None:
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [entry.model_dump(mode="json") for entry in entries.values()]}
        self.storage_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def stats(self) -> Dict[str, Any]:
        return {"backend": self.backend_name, "storage_path": str(self.storage_path) if self.storage_path else None}


class SQLiteSemanticCacheBackend:
    backend_name = "sqlite"

    def __init__(self, storage_path: str | Path) -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def load(self) -> dict[str, SemanticCacheEntry]:
        entries: dict[str, SemanticCacheEntry] = {}
        connection = sqlite3.connect(self.storage_path)
        try:
            rows = connection.execute(
                "SELECT namespace, key, payload FROM semantic_cache"
            ).fetchall()
            connection.commit()
        finally:
            connection.close()
        for namespace, key, payload in rows:
            entry = SemanticCacheEntry(**json.loads(payload))
            entries[f"{namespace}:{key}"] = entry
        return entries

    def save(self, entries: dict[str, SemanticCacheEntry]) -> None:
        self._ensure_schema()
        connection = sqlite3.connect(self.storage_path)
        try:
            connection.execute("DELETE FROM semantic_cache")
            connection.executemany(
                """
                INSERT INTO semantic_cache(namespace, key, query, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        entry.namespace,
                        entry.key,
                        entry.query,
                        json.dumps(entry.model_dump(mode="json")),
                        entry.updated_at.isoformat(),
                    )
                    for entry in entries.values()
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def cleanup_expired(self) -> int:
        entries = {
            key: entry
            for key, entry in self.load().items()
            if not self._is_expired(entry)
        }
        before = len(self.load())
        self.save(entries)
        return before - len(entries)

    def stats(self) -> Dict[str, Any]:
        connection = sqlite3.connect(self.storage_path)
        try:
            count = connection.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        return {"backend": self.backend_name, "storage_path": str(self.storage_path), "stored_rows": count}

    def _ensure_schema(self) -> None:
        connection = sqlite3.connect(self.storage_path)
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_cache (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    query TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, key)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_semantic_cache_namespace ON semantic_cache(namespace)"
            )
            connection.commit()
        finally:
            connection.close()

    def _is_expired(self, entry: SemanticCacheEntry) -> bool:
        if entry.ttl_seconds is None:
            return False
        age = datetime.now(timezone.utc) - entry.created_at
        return age.total_seconds() > entry.ttl_seconds


class RedisVectorSemanticCacheBackend:
    backend_name = "redis_vector"

    def __init__(self, url: str | None = None) -> None:
        self.url = url
        try:
            import redis  # type: ignore  # noqa: F401
        except Exception as exc:
            self.diagnostic = f"Redis backend unavailable: {exc}"
        else:
            self.diagnostic = "Redis backend dependency available but vector adapter is not configured"

    def load(self) -> dict[str, SemanticCacheEntry]:
        return {}

    def save(self, entries: dict[str, SemanticCacheEntry]) -> None:
        return None

    def stats(self) -> Dict[str, Any]:
        return {"backend": self.backend_name, "url": self.url, "diagnostic": self.diagnostic}


class SemanticCache:
    """Persistent TTL/staleness-aware semantic cache.

    Uses deterministic hashing embeddings by default so local tests do not require
    external embedding services. Redis/vector backends can implement the same
    get/put/invalidate/stats boundary later.
    """

    def __init__(
        self,
        threshold: float = 0.85,
        storage_path: str | Path | None = ".omni_memory/semantic_cache.json",
        embedding_dimensions: int = 64,
        backend: str | SemanticCacheBackend = "json",
        redis_url: str | None = None,
    ) -> None:
        self.threshold = threshold
        self.storage_path = Path(storage_path) if storage_path else None
        self.embedding_dimensions = embedding_dimensions
        self.embedding_strategy = CacheEmbeddingStrategy(embedding_dimensions)
        self.backend = self._backend(backend, storage_path, redis_url)
        self._entries: dict[str, SemanticCacheEntry] = {}
        self._load()

    def put(
        self,
        key: str,
        query: str,
        value: Dict[str, Any],
        *,
        namespace: str = "default",
        metadata: Dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        entry = SemanticCacheEntry(
            key=key,
            namespace=namespace,
            query=query,
            value=value,
            embedding=self._embedding(query),
            metadata=metadata or {},
            ttl_seconds=ttl_seconds,
        )
        self._entries[self._entry_key(namespace, key)] = entry
        self._persist()

    def get(
        self,
        query: str,
        *,
        namespace: str = "default",
        metadata: Dict[str, Any] | None = None,
    ) -> Optional[Dict[str, Any]]:
        lookup = self.lookup(query, namespace=namespace, metadata=metadata)
        if lookup.cache_hit and lookup.value is not None:
            return {
                **lookup.value,
                "cache_hit": True,
                "similarity": lookup.similarity,
                "cache_key": lookup.key,
            }
        return None

    def lookup(
        self,
        query: str,
        *,
        namespace: str = "default",
        metadata: Dict[str, Any] | None = None,
    ) -> SemanticCacheLookup:
        best_score = 0.0
        best: SemanticCacheEntry | None = None
        query_embedding = self._embedding(query)
        for entry in self._entries.values():
            if entry.namespace != namespace or self._is_expired(entry):
                continue
            if self._is_stale(entry, metadata or {}):
                continue
            score = max(
                self._similarity(query, entry.query),
                self._cosine(query_embedding, entry.embedding),
            )
            if score > best_score:
                best_score = score
                best = entry
        if best and best_score >= self.threshold:
            return SemanticCacheLookup(
                cache_hit=True,
                key=best.key,
                similarity=best_score,
                value=best.value,
            )
        return SemanticCacheLookup(cache_hit=False, similarity=best_score)

    def invalidate(
        self,
        *,
        namespace: str | None = None,
        key: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> int:
        removed = 0
        for entry_key, entry in list(self._entries.items()):
            if namespace is not None and entry.namespace != namespace:
                continue
            if key is not None and entry.key != key:
                continue
            if metadata and not all(entry.metadata.get(k) == v for k, v in metadata.items()):
                continue
            del self._entries[entry_key]
            removed += 1
        if removed:
            self._persist()
        return removed

    def stats(self) -> Dict[str, Any]:
        namespaces: dict[str, int] = {}
        expired = 0
        for entry in self._entries.values():
            namespaces[entry.namespace] = namespaces.get(entry.namespace, 0) + 1
            if self._is_expired(entry):
                expired += 1
        return {
            "entries": len(self._entries),
            "expired_entries": expired,
            "namespaces": namespaces,
            "threshold": self.threshold,
            "storage_path": str(self.storage_path) if self.storage_path else None,
            "backend": self.backend.stats(),
        }

    def _similarity(self, left: str, right: str) -> float:
        left_tokens = set(re.findall(r"[a-z0-9]+", left.lower()))
        right_tokens = set(re.findall(r"[a-z0-9]+", right.lower()))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _embedding(self, text: str) -> list[float]:
        return self.embedding_strategy.embed(text)

    def _cosine(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = sum(a * a for a in left) ** 0.5
        right_norm = sum(b * b for b in right) ** 0.5
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return numerator / (left_norm * right_norm)

    def _is_expired(self, entry: SemanticCacheEntry) -> bool:
        if entry.ttl_seconds is None:
            return False
        age = datetime.now(timezone.utc) - entry.created_at
        return age.total_seconds() > entry.ttl_seconds

    def _is_stale(self, entry: SemanticCacheEntry, metadata: Dict[str, Any]) -> bool:
        for key in ["model", "provider", "document_version", "corpus_version"]:
            if key in metadata and entry.metadata.get(key) not in (None, metadata[key]):
                return True
        return False

    def _entry_key(self, namespace: str, key: str) -> str:
        return f"{namespace}:{key}"

    def _load(self) -> None:
        try:
            for entry in self.backend.load().values():
                if not self._is_expired(entry):
                    self._entries[self._entry_key(entry.namespace, entry.key)] = entry
        except Exception:
            self._entries = {}

    def _persist(self) -> None:
        self._entries = {
            key: entry
            for key, entry in self._entries.items()
            if not self._is_expired(entry)
        }
        self.backend.save(self._entries)

    def _backend(
        self,
        backend: str | SemanticCacheBackend,
        storage_path: str | Path | None,
        redis_url: str | None,
    ) -> SemanticCacheBackend:
        if not isinstance(backend, str):
            return backend
        if backend == "sqlite":
            path = storage_path or ".omni_memory/semantic_cache.sqlite3"
            return SQLiteSemanticCacheBackend(path)
        if backend in {"redis", "redis_vector"}:
            return RedisVectorSemanticCacheBackend(redis_url)
        return JsonSemanticCacheBackend(storage_path)
