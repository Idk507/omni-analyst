from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Iterable

from omni_analyst.models.contracts import FilesystemPermissionMode, FilesystemPermissionRule


class PermissionDenied(PermissionError):
    pass


class FilesystemPermissionEvaluator:
    """First-match-wins permission evaluator for Deep Agent VFS tools."""

    def __init__(self, rules: Iterable[FilesystemPermissionRule] | None = None) -> None:
        self.rules = list(rules or [])

    def assert_allowed(self, operation: str, path: str) -> None:
        if not self.allowed(operation, path):
            raise PermissionDenied(f"{operation} denied for {path}")

    def allowed(self, operation: str, path: str) -> bool:
        normalized = self._normalize(path)
        for rule in self.rules:
            if rule.operations and operation not in rule.operations:
                continue
            if not rule.paths or any(fnmatch(normalized, self._normalize(pattern)) for pattern in rule.paths):
                return rule.mode == FilesystemPermissionMode.ALLOW
        return True

    def _normalize(self, path: str) -> str:
        raw = path.replace("\\", "/")
        if not raw.startswith("/"):
            raw = f"/{raw}"
        return PurePosixPath(raw).as_posix()
