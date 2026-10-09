from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from omni_analyst.models.contracts import (
    AutonomousAgentConfig,
    AutonomousApprovalPolicy,
    AutonomousSandboxMode,
)


class AutonomousConfigLoader:
    """Codex/OpenClaude-inspired layered config and project instruction loader."""

    def load(self, workspace_root: str | Path, *, profile: str = "default") -> AutonomousAgentConfig:
        root = Path(workspace_root)
        raw: dict[str, Any] = {}
        for path in [
            root / ".omniagent" / "config.json",
            root / ".codex" / "config.json",
        ]:
            if path.exists():
                raw.update(json.loads(path.read_text(encoding="utf-8")))
        profiles = raw.get("profiles", {})
        selected = profiles.get(profile, {}) if isinstance(profiles, dict) else {}
        merged = {**raw, **selected}
        return AutonomousAgentConfig(
            provider_id=merged.get("provider_id", merged.get("model_provider", "local_echo")),
            model=merged.get("model"),
            sandbox_mode=AutonomousSandboxMode(merged.get("sandbox_mode", "workspace-write")),
            approval_policy=AutonomousApprovalPolicy(merged.get("approval_policy", "on-request")),
            writable_roots=list(merged.get("writable_roots", [])),
            allowed_tools=list(merged.get("allowed_tools", [])),
            profile=profile,
            project_doc_max_bytes=int(merged.get("project_doc_max_bytes", 32768)),
            raw=raw,
        )

    def instruction_files(self, workspace_root: str | Path, *, max_bytes: int = 32768) -> List[Dict[str, Any]]:
        root = Path(workspace_root)
        candidates = [
            root / "AGENTS.override.md",
            root / "AGENTS.md",
            root / "CLAUDE.md",
            root / ".github" / "copilot-instructions.md",
        ]
        cursor_rules = root / ".cursor" / "rules"
        if cursor_rules.exists():
            candidates.extend(sorted(cursor_rules.glob("*.md")))
            candidates.extend(sorted(cursor_rules.glob("*.mdc")))
        loaded: list[dict[str, Any]] = []
        remaining = max_bytes
        for path in candidates:
            if not path.exists() or remaining <= 0:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")[:remaining]
            remaining -= len(content.encode("utf-8"))
            loaded.append({"path": str(path), "content": content})
        return loaded
