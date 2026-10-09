from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from omni_analyst.models.contracts import SandboxCommand, SandboxResult


class SandboxExecutor:
    """Constrained local sandbox runner for deterministic evaluation commands."""

    def __init__(self, sandbox_id: str = "local-sandbox") -> None:
        self.sandbox_id = sandbox_id

    def run(self, command: SandboxCommand) -> SandboxResult:
        start = time.perf_counter()
        cwd = str(Path(command.cwd or ".").resolve())
        env = os.environ.copy()
        env.update(command.env)
        try:
            process = subprocess.run(
                command.command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=command.timeout_seconds,
                check=False,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            return SandboxResult(
                sandbox_id=self.sandbox_id,
                command=command.command,
                exit_code=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
                duration_ms=duration_ms,
                timed_out=False,
                evaluation=self._evaluate(process.returncode, process.stdout, process.stderr),
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return SandboxResult(
                sandbox_id=self.sandbox_id,
                command=command.command,
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "Command timed out",
                duration_ms=duration_ms,
                timed_out=True,
                evaluation={
                    "passed": False,
                    "failure_type": "timeout",
                    "summary": "Sandbox command timed out.",
                },
            )

    def _evaluate(self, exit_code: int, stdout: str, stderr: str) -> dict:
        output = f"{stdout}\n{stderr}".lower()
        if exit_code == 0:
            return {
                "passed": True,
                "failure_type": None,
                "summary": "Command completed successfully.",
            }
        if "assert" in output or "failed" in output:
            failure_type = "test_failure"
        elif "syntaxerror" in output or "importerror" in output:
            failure_type = "runtime_error"
        else:
            failure_type = "command_failure"
        return {
            "passed": False,
            "failure_type": failure_type,
            "summary": "Command failed in sandbox.",
        }
