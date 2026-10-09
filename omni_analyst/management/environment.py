from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Iterable

from omni_analyst.models.contracts import EnvironmentSnapshot


class EnvironmentMonitor:
    """Collects lightweight runtime health signals for managed agents."""

    def snapshot(
        self,
        *,
        session_id: str | None = None,
        cwd: str | None = None,
        required_tools: Iterable[str] | None = None,
        sandbox_active: bool = False,
        browser_active: bool = False,
    ) -> EnvironmentSnapshot:
        active_cwd = str(Path(cwd or ".").resolve())
        tools = list(required_tools or ["python", "git"])
        missing = [tool for tool in tools if shutil.which(tool) is None]
        warnings = [f"Missing required tool: {tool}" for tool in missing]
        return EnvironmentSnapshot(
            session_id=session_id,
            cwd=active_cwd,
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            available_tools=[tool for tool in tools if tool not in missing],
            sandbox_active=sandbox_active,
            browser_active=browser_active,
            health="DEGRADED" if warnings else "OK",
            warnings=warnings,
        )
