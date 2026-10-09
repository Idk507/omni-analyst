from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


class MarkdownMemoryStore:
    """OpenClaw/Hermes-style markdown memory files in the workspace."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.memory_file = self.root / "MEMORY.md"
        self.daily_dir = self.root / "memory"

    def load_core(self) -> Dict[str, Any]:
        return {
            "path": str(self.memory_file),
            "content": self.memory_file.read_text(encoding="utf-8", errors="ignore")
            if self.memory_file.exists()
            else "",
        }

    def load_recent_daily(self, days: int = 2) -> List[Dict[str, Any]]:
        output: list[dict[str, Any]] = []
        today = datetime.now(timezone.utc).date()
        for offset in range(days):
            path = self.daily_dir / f"{today - timedelta(days=offset)}.md"
            if path.exists():
                output.append(
                    {
                        "path": str(path),
                        "content": path.read_text(encoding="utf-8", errors="ignore"),
                    }
                )
        return output

    def append_daily(self, title: str, content: str) -> Dict[str, Any]:
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        path = self.daily_dir / f"{datetime.now(timezone.utc).date()}.md"
        entry = f"\n## {title}\n\n{content.strip()}\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(entry)
        return {"path": str(path), "content": entry}

    def remember(self, fact: str) -> Dict[str, Any]:
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        entry = f"\n- {fact.strip()}\n"
        with self.memory_file.open("a", encoding="utf-8") as handle:
            handle.write(entry)
        return {"path": str(self.memory_file), "content": entry}

    def search(self, query: str, *, limit: int = 5) -> List[Dict[str, Any]]:
        tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
        candidates = [self.load_core()] + self.load_recent_daily(days=30)
        ranked: list[dict[str, Any]] = []
        for item in candidates:
            content = item.get("content", "")
            content_tokens = set(re.findall(r"[a-z0-9]+", content.lower()))
            if not tokens or not content_tokens:
                continue
            score = len(tokens & content_tokens) / len(tokens | content_tokens)
            if score > 0:
                ranked.append({**item, "score": round(score, 4)})
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]
