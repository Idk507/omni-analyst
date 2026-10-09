from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List

from omni_analyst.deep_agents.permissions import FilesystemPermissionEvaluator
from omni_analyst.memory.fabric import MemoryFabric


class VirtualFilesystemError(RuntimeError):
    pass


class VirtualFilesystemBackend:
    def ls(self, path: str = ".") -> List[Dict[str, Any]]:
        raise NotImplementedError

    def read_file(self, path: str, *, offset: int = 0, limit: int | None = None) -> Dict[str, Any]:
        raise NotImplementedError

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        raise NotImplementedError

    def edit_file(self, path: str, old_string: str, new_string: str, *, replace_all: bool = False) -> Dict[str, Any]:
        raise NotImplementedError

    def glob(self, pattern: str) -> List[str]:
        raise NotImplementedError

    def grep(self, pattern: str, path: str = ".") -> List[Dict[str, Any]]:
        raise NotImplementedError


class StateBackend(VirtualFilesystemBackend):
    def __init__(self, permissions: FilesystemPermissionEvaluator | None = None) -> None:
        self.files: dict[str, str] = {}
        self.permissions = permissions or FilesystemPermissionEvaluator()

    def ls(self, path: str = ".") -> List[Dict[str, Any]]:
        self.permissions.assert_allowed("read", path)
        prefix = self._key(path).rstrip("/")
        return [
            {"path": key, "size": len(value), "type": "file"}
            for key, value in sorted(self.files.items())
            if key.startswith(prefix)
        ]

    def read_file(self, path: str, *, offset: int = 0, limit: int | None = None) -> Dict[str, Any]:
        self.permissions.assert_allowed("read", path)
        key = self._key(path)
        if key not in self.files:
            raise FileNotFoundError(path)
        lines = self.files[key].splitlines()
        selected = lines[offset : offset + limit if limit is not None else None]
        return {"path": key, "content": "\n".join(selected), "line_count": len(lines)}

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        self.permissions.assert_allowed("write", path)
        key = self._key(path)
        self.files[key] = content
        return {"path": key, "bytes": len(content.encode("utf-8"))}

    def edit_file(self, path: str, old_string: str, new_string: str, *, replace_all: bool = False) -> Dict[str, Any]:
        self.permissions.assert_allowed("write", path)
        key = self._key(path)
        content = self.files.get(key, "")
        if old_string not in content:
            raise ValueError("old_string not found")
        self.files[key] = content.replace(old_string, new_string, -1 if replace_all else 1)
        return {"path": key, "replacements": content.count(old_string) if replace_all else 1}

    def glob(self, pattern: str) -> List[str]:
        normalized = self._key(pattern)
        return [key for key in sorted(self.files) if Path(key).match(normalized)]

    def grep(self, pattern: str, path: str = ".") -> List[Dict[str, Any]]:
        self.permissions.assert_allowed("read", path)
        regex = re.compile(pattern)
        results: list[dict[str, Any]] = []
        prefix = self._key(path).rstrip("/")
        for key, content in sorted(self.files.items()):
            if prefix != "." and not key.startswith(prefix):
                continue
            for index, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    results.append({"path": key, "line": index, "content": line})
        return results

    def _key(self, path: str) -> str:
        cleaned = path.replace("\\", "/").lstrip("/")
        return cleaned or "."


class LocalFilesystemBackend(VirtualFilesystemBackend):
    def __init__(
        self,
        root: str | Path,
        permissions: FilesystemPermissionEvaluator | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.permissions = permissions or FilesystemPermissionEvaluator()

    def ls(self, path: str = ".") -> List[Dict[str, Any]]:
        self.permissions.assert_allowed("read", path)
        target = self._resolve(path)
        return [
            {
                "path": item.relative_to(self.root).as_posix(),
                "size": item.stat().st_size,
                "type": "dir" if item.is_dir() else "file",
                "modified": item.stat().st_mtime,
            }
            for item in sorted(target.iterdir())
        ]

    def read_file(self, path: str, *, offset: int = 0, limit: int | None = None) -> Dict[str, Any]:
        self.permissions.assert_allowed("read", path)
        target = self._resolve(path)
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        selected = lines[offset : offset + limit if limit is not None else None]
        return {"path": target.relative_to(self.root).as_posix(), "content": "\n".join(selected), "line_count": len(lines)}

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        self.permissions.assert_allowed("write", path)
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": target.relative_to(self.root).as_posix(), "bytes": len(content.encode("utf-8"))}

    def edit_file(self, path: str, old_string: str, new_string: str, *, replace_all: bool = False) -> Dict[str, Any]:
        self.permissions.assert_allowed("write", path)
        target = self._resolve(path)
        content = target.read_text(encoding="utf-8")
        if old_string not in content:
            raise ValueError("old_string not found")
        replacements = content.count(old_string) if replace_all else 1
        target.write_text(content.replace(old_string, new_string, -1 if replace_all else 1), encoding="utf-8")
        return {"path": target.relative_to(self.root).as_posix(), "replacements": replacements}

    def glob(self, pattern: str) -> List[str]:
        self.permissions.assert_allowed("read", ".")
        return [
            item.relative_to(self.root).as_posix()
            for item in sorted(self.root.glob(pattern))
            if item.is_file()
        ]

    def grep(self, pattern: str, path: str = ".") -> List[Dict[str, Any]]:
        self.permissions.assert_allowed("read", path)
        target = self._resolve(path)
        regex = re.compile(pattern)
        roots = [target] if target.is_file() else [item for item in target.rglob("*") if item.is_file()]
        results: list[dict[str, Any]] = []
        for item in roots:
            try:
                lines = item.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for index, line in enumerate(lines, start=1):
                if regex.search(line):
                    results.append({"path": item.relative_to(self.root).as_posix(), "line": index, "content": line})
        return results

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if os.path.commonpath([str(self.root), str(target)]) != str(self.root):
            raise VirtualFilesystemError(f"Path escapes workspace: {path}")
        return target


class MemoryBackend(StateBackend):
    def __init__(self, memory_fabric: MemoryFabric) -> None:
        super().__init__()
        self.memory_fabric = memory_fabric
        self.write_file("memories/export.json", __import__("json").dumps(memory_fabric.export_state(), indent=2))


class CompositeBackend(VirtualFilesystemBackend):
    def __init__(self, default: VirtualFilesystemBackend, routes: Dict[str, VirtualFilesystemBackend] | None = None) -> None:
        self.default = default
        self.routes = routes or {}

    def _backend(self, path: str) -> tuple[VirtualFilesystemBackend, str]:
        normalized = path.replace("\\", "/").lstrip("/")
        for prefix, backend in sorted(self.routes.items(), key=lambda item: len(item[0]), reverse=True):
            clean_prefix = prefix.strip("/")
            if normalized.startswith(clean_prefix):
                return backend, normalized[len(clean_prefix) :].lstrip("/") or "."
        return self.default, path

    def ls(self, path: str = ".") -> List[Dict[str, Any]]:
        backend, routed = self._backend(path)
        return backend.ls(routed)

    def read_file(self, path: str, *, offset: int = 0, limit: int | None = None) -> Dict[str, Any]:
        backend, routed = self._backend(path)
        return backend.read_file(routed, offset=offset, limit=limit)

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        backend, routed = self._backend(path)
        return backend.write_file(routed, content)

    def edit_file(self, path: str, old_string: str, new_string: str, *, replace_all: bool = False) -> Dict[str, Any]:
        backend, routed = self._backend(path)
        return backend.edit_file(routed, old_string, new_string, replace_all=replace_all)

    def glob(self, pattern: str) -> List[str]:
        return self.default.glob(pattern)

    def grep(self, pattern: str, path: str = ".") -> List[Dict[str, Any]]:
        backend, routed = self._backend(path)
        return backend.grep(pattern, routed)
