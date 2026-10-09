from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


UNSAFE_NAMES = {".env", "credentials.json", "secrets.json"}
UNSAFE_FRAGMENTS = ("secret", "credential", "private_key")


@dataclass
class PatchOperationResult:
    path: str
    operation: str
    applied: bool
    diff: str = ""
    error: str | None = None


@dataclass
class PatchApplyResult:
    applied: bool
    operations: list[PatchOperationResult] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    rollback_performed: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "operations": [operation.__dict__ for operation in self.operations],
            "changed_files": self.changed_files,
            "rollback_performed": self.rollback_performed,
            "error": self.error,
        }


class CodePatchEngine:
    """Applies scoped, reviewable file patches for Codex/Claude Code-style workflows."""

    def apply(
        self,
        repository_path: str,
        operations: list[dict[str, Any]],
        *,
        allowed_files: list[str] | None = None,
    ) -> PatchApplyResult:
        root = Path(repository_path).resolve()
        allowed = {self._normalize_allowed(path) for path in allowed_files or []}
        backups: dict[Path, str | None] = {}
        results: list[PatchOperationResult] = []
        changed_files: list[str] = []
        last_operation: dict[str, Any] = {}

        try:
            for operation in operations:
                last_operation = operation
                op_type = str(operation.get("type") or operation.get("operation") or "")
                relative_path = str(operation.get("path") or "")
                target = self._resolve_target(root, relative_path, allowed)
                if target not in backups:
                    backups[target] = target.read_text(encoding="utf-8") if target.exists() else None
                before = target.read_text(encoding="utf-8") if target.exists() else ""
                after = self._apply_one(target, op_type, operation, before)
                diff = self._diff(relative_path, before, after)
                results.append(PatchOperationResult(path=relative_path, operation=op_type, applied=True, diff=diff))
                normalized = self._normalize_allowed(relative_path)
                if normalized not in changed_files:
                    changed_files.append(normalized)
            return PatchApplyResult(applied=True, operations=results, changed_files=changed_files)
        except (OSError, ValueError) as exc:
            self._rollback(backups)
            failed_path = str(last_operation.get("path", ""))
            failed_type = str(last_operation.get("type", ""))
            results.append(PatchOperationResult(path=failed_path, operation=failed_type, applied=False, error=str(exc)))
            return PatchApplyResult(
                applied=False,
                operations=results,
                changed_files=changed_files,
                rollback_performed=True,
                error=str(exc),
            )

    def operations_from_json(self, text: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            operations = payload.get("patch_operations") or payload.get("operations") or []
            return operations if isinstance(operations, list) else []
        return payload if isinstance(payload, list) else []

    def _resolve_target(self, root: Path, relative_path: str, allowed: set[str]) -> Path:
        if not relative_path:
            raise ValueError("Patch operation missing path")
        normalized = self._normalize_allowed(relative_path)
        target = (root / normalized).resolve()
        if root != target and root not in target.parents:
            raise PermissionError(f"Patch path escapes repository: {relative_path}")
        if self._is_unsafe(normalized):
            raise PermissionError(f"Refusing to patch sensitive file: {relative_path}")
        if allowed and normalized not in allowed:
            raise PermissionError(f"Patch path is outside changed_files scope: {relative_path}")
        return target

    def _apply_one(self, target: Path, op_type: str, operation: dict[str, Any], before: str) -> str:
        if op_type == "write_file":
            after = str(operation.get("content", ""))
        elif op_type == "append_file":
            after = before + str(operation.get("content", ""))
        elif op_type == "replace_text":
            old_text = str(operation.get("old_text", ""))
            new_text = str(operation.get("new_text", ""))
            if not old_text:
                raise ValueError("replace_text operation requires old_text")
            if old_text not in before:
                raise ValueError(f"old_text not found in {target}")
            after = before.replace(old_text, new_text, 1)
        else:
            raise ValueError(f"Unsupported patch operation: {op_type}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(after, encoding="utf-8")
        return after

    def _rollback(self, backups: dict[Path, str | None]) -> None:
        for path, content in backups.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

    def _diff(self, relative_path: str, before: str, after: str) -> str:
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
            )
        )

    def _normalize_allowed(self, path: str) -> str:
        return Path(path.replace("\\", "/")).as_posix().lstrip("/")

    def _is_unsafe(self, path: str) -> bool:
        lowered = path.lower()
        name = Path(path).name.lower()
        return name in UNSAFE_NAMES or any(fragment in lowered for fragment in UNSAFE_FRAGMENTS)
