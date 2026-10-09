from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


class WorkspaceManager:
    """Manages per-run sandbox workspaces under a configurable root."""

    def __init__(self, root: str | None = None) -> None:
        default_root = os.getenv(
            "OMNI_AGENT_WORKSPACES",
            str(Path(tempfile.gettempdir()) / "omni_workspaces"),
        )
        self.root = Path(root or default_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, workspace_id: str) -> "Workspace":
        path = self.root / workspace_id
        path.mkdir(parents=True, exist_ok=True)
        return Workspace(workspace_id=workspace_id, path=path)

    def get(self, workspace_id: str) -> "Workspace":
        path = self.root / workspace_id
        if not path.exists():
            raise FileNotFoundError(f"Workspace not found: {workspace_id}")
        return Workspace(workspace_id=workspace_id, path=path)

    def list(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for entry in sorted(self.root.iterdir()) if self.root.exists() else []:
            if entry.is_dir():
                stat = entry.stat()
                created = getattr(stat, "st_birthtime", stat.st_mtime)
                items.append(
                    {
                        "workspace_id": entry.name,
                        "created_at": datetime.fromtimestamp(created, tz=timezone.utc).isoformat(),
                    }
                )
        return items

    def remove(self, workspace_id: str) -> None:
        path = self.root / workspace_id
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


class Workspace:
    """A single sandbox workspace folder."""

    def __init__(self, workspace_id: str, path: Path) -> None:
        self.workspace_id = workspace_id
        self.path = path

    def resolve(self, relative: str) -> Path:
        target = (self.path / relative).resolve()
        try:
            target.relative_to(self.path)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {relative}") from exc
        return target

    def write_file(self, relative: str, content: str) -> Dict[str, Any]:
        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "path": relative,
            "absolute": str(target),
            "bytes": target.stat().st_size,
        }

    def read_file(self, relative: str, max_bytes: int = 32_000) -> Dict[str, Any]:
        target = self.resolve(relative)
        data = target.read_bytes()
        truncated = False
        if len(data) > max_bytes:
            data = data[:max_bytes]
            truncated = True
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")
        return {"path": relative, "content": text, "truncated": truncated}

    def list_files(self, relative: str = ".", limit: int = 200) -> Dict[str, Any]:
        base = self.resolve(relative)
        out: List[Dict[str, Any]] = []
        if not base.exists():
            return {"path": relative, "files": [], "exists": False}
        for path in sorted(base.rglob("*")):
            if path.is_file():
                rel = path.relative_to(self.path).as_posix()
                out.append({"path": rel, "size": path.stat().st_size})
                if len(out) >= limit:
                    break
        return {"path": relative, "files": out, "exists": True}

    def snapshot(self) -> Dict[str, Any]:
        files = self.list_files(".", limit=500)["files"]
        return {
            "workspace_id": self.workspace_id,
            "path": str(self.path),
            "file_count": len(files),
            "files": files,
        }

    def manifest_path(self) -> Path:
        return self.path / ".omni_manifest.json"

    def write_manifest(self, payload: Dict[str, Any]) -> None:
        self.manifest_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
