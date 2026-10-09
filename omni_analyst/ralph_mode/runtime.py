from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from omni_analyst.models.contracts import (
    RalphIterationRecord,
    RalphModeRequest,
    RalphModeRun,
    RalphModeStatus,
)
from omni_analyst.time_travel.checkpoint import CheckpointStore


class RalphModeRuntime:
    """Autonomous fresh-context loop for long-running coding tasks.

    Ralph Mode intentionally avoids carrying chat history between iterations.
    Each pass rehydrates only from filesystem artifacts: PROMPT.md, state,
    progress, checkpoints, and verification output.
    """

    def __init__(self, checkpoint_store: CheckpointStore | None = None) -> None:
        self.checkpoint_store = checkpoint_store or CheckpointStore()
        self._runs: dict[str, RalphModeRun] = {}

    def run(self, request: RalphModeRequest) -> RalphModeRun:
        if request.iterations < 1:
            raise ValueError("iterations must be at least 1")
        run = self._initialize_run(request)
        run.status = RalphModeStatus.RUNNING
        self._persist_run(run)
        for iteration in range(1, request.iterations + 1):
            record = self._run_iteration(run, iteration)
            run.iterations.append(record)
            self._write_progress(run)
            self._persist_run(run)
            if record.status == RalphModeStatus.FAILED:
                run.status = RalphModeStatus.FAILED
                break
            if request.stop_on_success and self._is_success(record, request.success_markers):
                run.status = RalphModeStatus.COMPLETED
                break
        if run.status == RalphModeStatus.RUNNING:
            run.status = RalphModeStatus.COMPLETED
        run.completed_at = self._now_dt()
        run.summary = self._summarize(run)
        self._persist_run(run)
        self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> RalphModeRun:
        if run_id in self._runs:
            return self._runs[run_id]
        path = self._find_run_state(run_id)
        if path is None:
            raise KeyError(f"Unknown Ralph run: {run_id}")
        run = RalphModeRun(**json.loads(path.read_text(encoding="utf-8")))
        self._runs[run.run_id] = run
        return run

    def list_runs(self) -> List[RalphModeRun]:
        return sorted(self._runs.values(), key=lambda run: run.created_at)

    def _initialize_run(self, request: RalphModeRequest) -> RalphModeRun:
        run = RalphModeRun(request=request)
        run_root = Path(request.run_dir) / run.run_id
        run_root.mkdir(parents=True, exist_ok=True)
        run.run_root = str(run_root)
        run.prompt_path = str(run_root / "PROMPT.md")
        run.state_path = str(run_root / "RALPH_STATE.json")
        run.progress_path = str(run_root / "PROGRESS.md")
        Path(run.prompt_path).write_text(self._base_prompt(request), encoding="utf-8")
        Path(run.progress_path).write_text("# Ralph Mode Progress\n\n", encoding="utf-8")
        return run

    def _run_iteration(self, run: RalphModeRun, iteration: int) -> RalphIterationRecord:
        started = time.perf_counter()
        prompt_path = Path(run.run_root) / f"iteration-{iteration:03d}-prompt.md"
        prompt = self._iteration_prompt(run, iteration)
        prompt_path.write_text(prompt, encoding="utf-8")
        record = RalphIterationRecord(
            iteration=iteration,
            prompt_path=str(prompt_path),
            status=RalphModeStatus.RUNNING,
        )
        try:
            checkpoint = self.checkpoint_store.save(
                self._workspace_state(run, iteration),
                metadata={"ralph_run_id": run.run_id, "iteration": iteration},
            )
            record.checkpoint_id = checkpoint.checkpoint_id
            if run.request.agent_command:
                completed = subprocess.run(
                    run.request.agent_command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    cwd=run.request.workspace_root,
                    timeout=run.request.timeout_seconds,
                    check=False,
                )
                record.stdout = completed.stdout
                record.stderr = completed.stderr
                record.exit_code = completed.returncode
                record.status = (
                    RalphModeStatus.COMPLETED
                    if completed.returncode == 0
                    else RalphModeStatus.FAILED
                )
            else:
                output = self._local_planning_pass(run, iteration)
                record.stdout = json.dumps(output, indent=2)
                record.exit_code = 0
                record.status = RalphModeStatus.COMPLETED
            record.verification = self._verify(run)
            if any(not item["ok"] for item in record.verification):
                record.status = RalphModeStatus.FAILED
            record.summary = self._iteration_summary(record)
        except subprocess.TimeoutExpired as exc:
            record.stdout = exc.stdout or ""
            record.stderr = exc.stderr or ""
            record.status = RalphModeStatus.FAILED
            record.summary = {"error": f"agent timed out after {run.request.timeout_seconds}s"}
        except Exception as exc:
            record.status = RalphModeStatus.FAILED
            record.summary = {"error": str(exc)}
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        record.completed_at = self._now_dt()
        return record

    def _verify(self, run: RalphModeRun) -> List[Dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for command in run.request.verification_commands:
            started = time.perf_counter()
            try:
                completed = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    cwd=run.request.workspace_root,
                    timeout=run.request.timeout_seconds,
                    check=False,
                )
                results.append(
                    {
                        "command": command,
                        "ok": completed.returncode == 0,
                        "exit_code": completed.returncode,
                        "stdout": completed.stdout,
                        "stderr": completed.stderr,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                    }
                )
            except subprocess.TimeoutExpired as exc:
                results.append(
                    {
                        "command": command,
                        "ok": False,
                        "exit_code": None,
                        "stdout": exc.stdout or "",
                        "stderr": exc.stderr or "",
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "error": "verification timed out",
                    }
                )
        return results

    def _local_planning_pass(self, run: RalphModeRun, iteration: int) -> Dict[str, Any]:
        progress = Path(run.progress_path)
        line = (
            f"- Iteration {iteration}: inspected fresh prompt and persisted state; "
            "no external agent_command configured.\n"
        )
        with progress.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return {
            "mode": "local_planning_pass",
            "fresh_context": True,
            "next_action": "configure agent_command for code execution, or use progress artifacts for planning",
            "progress_path": run.progress_path,
        }

    def _base_prompt(self, request: RalphModeRequest) -> str:
        return (
            "# Ralph Mode Prompt\n\n"
            "Work autonomously in fresh-context iterations. Use filesystem state, "
            "checkpoints, progress files, and verification results as memory.\n\n"
            "## User Goal\n"
            f"{request.prompt.strip()}\n"
        )

    def _iteration_prompt(self, run: RalphModeRun, iteration: int) -> str:
        progress = Path(run.progress_path).read_text(encoding="utf-8")
        state = self._workspace_state(run, iteration)
        return (
            f"{Path(run.prompt_path).read_text(encoding='utf-8')}\n\n"
            f"## Iteration\n{iteration}\n\n"
            "## Ralph Rules\n"
            "- Treat this as a fresh context run.\n"
            "- Read repository files before assuming prior decisions.\n"
            "- Make forward progress, verify, and leave concise progress notes.\n"
            "- Write RALPH_DONE in output only when the user goal is complete.\n\n"
            f"## Current Progress\n{progress}\n\n"
            f"## Workspace State\n```json\n{json.dumps(state, indent=2)}\n```\n"
        )

    def _workspace_state(self, run: RalphModeRun, iteration: int) -> Dict[str, Any]:
        workspace = Path(run.request.workspace_root)
        return {
            "run_id": run.run_id,
            "iteration": iteration,
            "workspace_root": str(workspace),
            "prompt_path": run.prompt_path,
            "progress_path": run.progress_path,
            "existing_iterations": len(run.iterations),
            "metadata": run.request.metadata,
        }

    def _write_progress(self, run: RalphModeRun) -> None:
        with Path(run.progress_path).open("a", encoding="utf-8") as handle:
            latest = run.iterations[-1]
            handle.write(
                f"- Iteration {latest.iteration}: {latest.status.value}; "
                f"checkpoint={latest.checkpoint_id}; exit={latest.exit_code}\n"
            )

    def _persist_run(self, run: RalphModeRun) -> None:
        if not run.state_path:
            return
        Path(run.state_path).write_text(run.model_dump_json(indent=2), encoding="utf-8")

    def _find_run_state(self, run_id: str) -> Path | None:
        for path in Path(".").glob(f"**/{run_id}/RALPH_STATE.json"):
            return path
        return None

    def _is_success(self, record: RalphIterationRecord, markers: List[str]) -> bool:
        combined = f"{record.stdout}\n{record.stderr}"
        return record.status == RalphModeStatus.COMPLETED and any(
            marker in combined for marker in markers
        )

    def _iteration_summary(self, record: RalphIterationRecord) -> Dict[str, Any]:
        return {
            "ok": record.status == RalphModeStatus.COMPLETED,
            "stdout_chars": len(record.stdout),
            "stderr_chars": len(record.stderr),
            "verification_count": len(record.verification),
            "verification_ok": all(item["ok"] for item in record.verification),
        }

    def _summarize(self, run: RalphModeRun) -> Dict[str, Any]:
        return {
            "iteration_count": len(run.iterations),
            "completed_iterations": sum(
                1 for item in run.iterations if item.status == RalphModeStatus.COMPLETED
            ),
            "failed_iterations": sum(
                1 for item in run.iterations if item.status == RalphModeStatus.FAILED
            ),
            "checkpoint_ids": [
                item.checkpoint_id for item in run.iterations if item.checkpoint_id
            ],
        }

    def _now_dt(self) -> datetime:
        return datetime.now(timezone.utc)
