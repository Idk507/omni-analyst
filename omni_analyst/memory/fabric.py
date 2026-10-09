from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
from typing import Any, Dict, List
from uuid import uuid4


class MemoryFabric:
    """Persistent representation of the v4/v5 memory layers.

    This mirrors common agent memory systems:
    - short-term session memory,
    - episodic event memory,
    - long-term user facts,
    - procedural user preferences,
    - document-scoped memory,
    - project instruction memory from files such as CLAUDE.md and AGENTS.md.
    """

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = Path(storage_path or ".omni_memory/memory.json")
        self._short_term: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._long_term: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._episodic: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._procedural: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._document: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._project: Dict[str, Any] = {}
        self._lock = RLock()
        self._load()

    def append_short_term(self, session_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            record = self._with_memory_metadata(item, namespace="short_term", owner_id=session_id)
            self._short_term[session_id].append(record)
            self._persist()
            return deepcopy(record)

    def append_episodic(self, session_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            record = self._with_memory_metadata(item, namespace="episodic", owner_id=session_id)
            self._episodic[session_id].append(record)
            self._persist()
            return deepcopy(record)

    def merge_long_term(self, user_id: str, item: Dict[str, Any]) -> None:
        with self._lock:
            self._long_term[user_id].update(item)
            self._long_term[user_id]["updated_at"] = self._now()
            self._long_term[user_id]["memory_id"] = self._stable_id("long_term", user_id, self._long_term[user_id])
            self._persist()

    def merge_procedural(self, user_id: str, item: Dict[str, Any]) -> None:
        with self._lock:
            self._procedural[user_id].update(item)
            self._procedural[user_id]["updated_at"] = self._now()
            self._procedural[user_id]["memory_id"] = self._stable_id("procedural", user_id, self._procedural[user_id])
            self._persist()

    def merge_document(self, document_id: str, item: Dict[str, Any]) -> None:
        with self._lock:
            self._document[document_id].update(item)
            self._document[document_id]["updated_at"] = self._now()
            self._document[document_id]["memory_id"] = self._stable_id("document", document_id, self._document[document_id])
            self._persist()

    def merge_project_memory(self, item: Dict[str, Any]) -> None:
        with self._lock:
            self._project.update(item)
            self._project["updated_at"] = self._now()
            self._project["memory_id"] = self._stable_id("project", "global", self._project)
            self._persist()

    def get_session_snapshot(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            return {
                "short_term": deepcopy(list(self._short_term[session_id])),
                "episodic": deepcopy(list(self._episodic[session_id])),
                "long_term": deepcopy(dict(self._long_term)),
                "procedural": deepcopy(dict(self._procedural)),
                "document": deepcopy(dict(self._document)),
                "project": deepcopy(dict(self._project)),
            }

    def get_user_snapshot(self, user_id: str) -> Dict[str, Any]:
        with self._lock:
            return {
                "long_term": deepcopy(dict(self._long_term[user_id])),
                "procedural": deepcopy(dict(self._procedural[user_id])),
            }

    def get_document_snapshot(self, document_id: str) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(dict(self._document[document_id]))

    def query(
        self,
        text: str,
        *,
        layers: List[str] | None = None,
        owner_id: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            query_tokens = self._tokens(text)
            candidates = self._iter_records(layers=layers, owner_id=owner_id)
            ranked: list[dict[str, Any]] = []
            for record in candidates:
                score = self._score(query_tokens, self._tokens(json.dumps(record, sort_keys=True)))
                if score <= 0:
                    continue
                ranked.append({**deepcopy(record), "score": round(score, 4)})
            return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]

    def compact_session(self, session_id: str, *, keep_last: int = 20) -> Dict[str, Any]:
        with self._lock:
            items = list(self._short_term[session_id])
            if len(items) <= keep_last:
                return {"compacted": False, "kept": len(items), "summary": ""}
            archived = items[:-keep_last]
            kept = items[-keep_last:]
            summary = self._summarize_records(archived)
            compact_record = self._with_memory_metadata(
                {
                    "type": "compaction_summary",
                    "summary": summary,
                    "source_count": len(archived),
                    "source_ids": [item.get("memory_id") for item in archived],
                },
                namespace="short_term",
                owner_id=session_id,
            )
            self._short_term[session_id] = [compact_record] + kept
            self._episodic[session_id].append(
                self._with_memory_metadata(
                    {"type": "memory_compacted", "source_count": len(archived)},
                    namespace="episodic",
                    owner_id=session_id,
                )
            )
            self._persist()
            return {"compacted": True, "kept": len(kept), "summary": summary}

    def export_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._payload()

    def import_state(self, payload: Dict[str, Any], *, merge: bool = True) -> Dict[str, Any]:
        with self._lock:
            if not merge:
                self._short_term.clear()
                self._long_term.clear()
                self._episodic.clear()
                self._procedural.clear()
                self._document.clear()
                self._project.clear()
            self._short_term.update(payload.get("short_term", {}))
            self._long_term.update(payload.get("long_term", {}))
            self._episodic.update(payload.get("episodic", {}))
            self._procedural.update(payload.get("procedural", {}))
            self._document.update(payload.get("document", {}))
            self._project.update(payload.get("project", {}))
            self._persist()
            return self.stats()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "short_term_sessions": len(self._short_term),
                "short_term_items": sum(len(items) for items in self._short_term.values()),
                "episodic_sessions": len(self._episodic),
                "episodic_items": sum(len(items) for items in self._episodic.values()),
                "long_term_users": len(self._long_term),
                "procedural_users": len(self._procedural),
                "documents": len(self._document),
                "project_keys": len(self._project),
                "storage_path": str(self.storage_path),
            }

    def load_project_instructions(self, root: str | Path = ".") -> Dict[str, Any]:
        root_path = Path(root)
        candidates = [
            root_path / "CLAUDE.md",
            root_path / "AGENTS.md",
            root_path / ".github" / "copilot-instructions.md",
        ]
        rules_dir = root_path / ".cursor" / "rules"
        if rules_dir.exists():
            candidates.extend(sorted(rules_dir.glob("*.md")))
            candidates.extend(sorted(rules_dir.glob("*.mdc")))

        loaded: list[dict[str, str]] = []
        for path in candidates:
            if not path.exists() or not path.is_file():
                continue
            loaded.append(
                {
                    "path": str(path),
                    "content": path.read_text(encoding="utf-8", errors="ignore"),
                }
            )

        project_memory = {
            "instruction_files": loaded,
            "loaded_at": self._now(),
        }
        self.merge_project_memory(project_memory)
        return project_memory

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.import_state(payload, merge=True)

    def _persist(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._payload()
        temp_path = self.storage_path.with_name(
            f"{self.storage_path.stem}.{uuid4().hex}.tmp"
        )
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.storage_path)

    def _payload(self) -> Dict[str, Any]:
        return {
            "schema_version": 2,
            "short_term": dict(self._short_term),
            "long_term": dict(self._long_term),
            "episodic": dict(self._episodic),
            "procedural": dict(self._procedural),
            "document": dict(self._document),
            "project": dict(self._project),
        }

    def _with_memory_metadata(
        self, item: Dict[str, Any], *, namespace: str, owner_id: str
    ) -> Dict[str, Any]:
        record = dict(item)
        record.setdefault("created_at", self._now())
        record.setdefault("namespace", namespace)
        record.setdefault("owner_id", owner_id)
        record.setdefault("memory_id", self._stable_id(namespace, owner_id, record))
        return record

    def _iter_records(
        self, *, layers: List[str] | None, owner_id: str | None
    ) -> List[Dict[str, Any]]:
        selected = set(layers or ["short_term", "episodic", "long_term", "procedural", "document", "project"])
        records: list[dict[str, Any]] = []
        if "short_term" in selected:
            for key, items in self._short_term.items():
                if owner_id is None or owner_id == key:
                    records.extend(items)
        if "episodic" in selected:
            for key, items in self._episodic.items():
                if owner_id is None or owner_id == key:
                    records.extend(items)
        for layer, store in [
            ("long_term", self._long_term),
            ("procedural", self._procedural),
            ("document", self._document),
        ]:
            if layer in selected:
                records.extend(
                    {**value, "namespace": layer, "owner_id": key}
                    for key, value in store.items()
                    if owner_id is None or owner_id == key
                )
        if "project" in selected and (owner_id is None or owner_id == "project"):
            records.append({**self._project, "namespace": "project", "owner_id": "project"})
        return records

    def _stable_id(self, namespace: str, owner_id: str, item: Dict[str, Any]) -> str:
        material = json.dumps(
            {key: value for key, value in item.items() if key not in {"memory_id", "updated_at"}},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(f"{namespace}:{owner_id}:{material}".encode("utf-8")).hexdigest()[:16]
        return f"mem-{digest}"

    def _tokens(self, text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower()))

    def _score(self, left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    def _summarize_records(self, records: List[Dict[str, Any]]) -> str:
        texts = []
        for record in records:
            text = record.get("query") or record.get("summary") or record.get("content") or record.get("type")
            if text:
                texts.append(str(text))
        return " | ".join(texts[:20])[:2000]

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

