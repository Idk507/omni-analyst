from __future__ import annotations

import json
import os
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Callable, Dict, List

from omni_analyst.models.contracts import (
    HookCommand,
    HookConfig,
    HookDecision,
    HookDecisionAction,
    HookEvent,
    HookExecutionRecord,
    HookMatcher,
    HookMode,
)


HookHandler = Callable[[Dict[str, Any]], Dict[str, Any] | None]


@dataclass
class HookResult:
    event: str
    handler: str
    output: Dict[str, Any] = field(default_factory=dict)


class HookRuntime:
    """Claude/Codex-style hook runtime.

    Supports legacy in-process handlers plus production command hooks with matchers,
    priority ordering, JSON stdin/stdout, timeout handling, and blocking decisions.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[str, HookHandler]]] = defaultdict(list)
        self._configs: dict[str, HookConfig] = {}
        self._python_handlers: dict[str, HookHandler] = {}
        self._records: list[HookExecutionRecord] = []

    def register(self, event: str, name: str, handler: HookHandler) -> None:
        self._handlers[event].append((name, handler))
        config = HookConfig(
            event=event,
            name=name,
            source="python",
            mode=HookMode.OBSERVE,
        )
        self._configs[config.hook_id] = config
        self._python_handlers[config.hook_id] = handler

    def register_config(
        self, config: HookConfig, handler: HookHandler | None = None
    ) -> HookConfig:
        self._configs[config.hook_id] = config
        if handler is not None:
            self._python_handlers[config.hook_id] = handler
        return config

    def register_command(
        self,
        *,
        event: HookEvent | str,
        name: str,
        command: List[str],
        matcher: HookMatcher | None = None,
        priority: int = 100,
        timeout_seconds: int = 10,
        mode: HookMode = HookMode.INTERVENE,
        fail_closed: bool = False,
        source: str = "runtime",
    ) -> HookConfig:
        config = HookConfig(
            event=event,
            name=name,
            matcher=matcher or HookMatcher(),
            mode=mode,
            priority=priority,
            fail_closed=fail_closed,
            source=source,
            command=HookCommand(command=command, timeout_seconds=timeout_seconds),
        )
        return self.register_config(config)

    def list_hooks(self) -> List[HookConfig]:
        return sorted(
            self._configs.values(),
            key=lambda item: (str(item.event), item.priority, item.hook_id),
        )

    def unregister(self, hook_id: str) -> None:
        config = self._configs.pop(hook_id, None)
        self._python_handlers.pop(hook_id, None)
        if config is not None:
            event_name = self._event_name(config.event)
            self._handlers[event_name] = [
                item for item in self._handlers.get(event_name, []) if item[0] != config.name
            ]

    def fire(self, event: str, payload: Dict[str, Any]) -> List[HookResult]:
        return [
            HookResult(event=record.event, handler=record.name, output=record.output_payload)
            for record in self.fire_detailed(event, payload)
        ]

    def fire_detailed(
        self, event: HookEvent | str, payload: Dict[str, Any]
    ) -> List[HookExecutionRecord]:
        event_name = event.value if isinstance(event, HookEvent) else event
        candidates = [
            config
            for config in self._configs.values()
            if config.enabled
            and self._event_name(config.event) == event_name
            and self._matches(config.matcher, event_name, payload)
        ]
        candidates.sort(key=lambda item: (item.priority, item.hook_id))
        records: list[HookExecutionRecord] = []
        current_payload = dict(payload)
        for config in candidates:
            record = self._execute_config(config, event_name, current_payload)
            records.append(record)
            self._records.append(record)
            if record.decision.payload_updates and config.composable:
                current_payload.update(record.decision.payload_updates)
            if not record.allowed or record.decision.action == HookDecisionAction.BLOCK:
                break
        return records

    def records(self) -> List[HookExecutionRecord]:
        return list(self._records)

    def _execute_config(
        self, config: HookConfig, event: str, payload: Dict[str, Any]
    ) -> HookExecutionRecord:
        started = time.perf_counter()
        try:
            if config.command:
                record = self._execute_command(config, event, payload)
            elif config.hook_id in self._python_handlers:
                output = self._python_handlers[config.hook_id](dict(payload)) or {}
                decision = self._decision_from_output(output)
                record = HookExecutionRecord(
                    hook_id=config.hook_id,
                    event=event,
                    name=config.name,
                    allowed=decision.action != HookDecisionAction.BLOCK,
                    decision=decision,
                    input_payload=payload,
                    output_payload=output,
                )
            else:
                record = HookExecutionRecord(
                    hook_id=config.hook_id,
                    event=event,
                    name=config.name,
                    allowed=True,
                    input_payload=payload,
                    output_payload={},
                )
        except Exception as exc:
            decision = HookDecision(
                action=HookDecisionAction.BLOCK if config.fail_closed else HookDecisionAction.WARN,
                reason=str(exc),
            )
            record = HookExecutionRecord(
                hook_id=config.hook_id,
                event=event,
                name=config.name,
                allowed=not config.fail_closed,
                decision=decision,
                input_payload=payload,
                error=str(exc),
            )
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        return record

    def _execute_command(
        self, config: HookConfig, event: str, payload: Dict[str, Any]
    ) -> HookExecutionRecord:
        assert config.command is not None
        env = os.environ.copy()
        env.update(config.command.env)
        command_input = {
            "event": event,
            "hook_id": config.hook_id,
            "name": config.name,
            "payload": payload,
        }
        try:
            completed = subprocess.run(
                config.command.command,
                input=json.dumps(command_input),
                text=True,
                capture_output=True,
                timeout=config.command.timeout_seconds,
                cwd=config.command.cwd,
                env=env,
                check=False,
            )
            output_payload = self._json_or_text(completed.stdout)
            decision = self._decision_from_output(output_payload)
            allowed = completed.returncode == 0 and decision.action != HookDecisionAction.BLOCK
            if completed.returncode != 0 and config.fail_closed:
                allowed = False
                decision = HookDecision(
                    action=HookDecisionAction.BLOCK,
                    reason=completed.stderr or f"Hook exited {completed.returncode}",
                )
            return HookExecutionRecord(
                hook_id=config.hook_id,
                event=event,
                name=config.name,
                allowed=allowed,
                decision=decision,
                input_payload=payload,
                output_payload=output_payload,
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
            )
        except subprocess.TimeoutExpired as exc:
            decision = HookDecision(
                action=HookDecisionAction.BLOCK if config.fail_closed else HookDecisionAction.WARN,
                reason=f"Hook timed out after {config.command.timeout_seconds}s",
            )
            return HookExecutionRecord(
                hook_id=config.hook_id,
                event=event,
                name=config.name,
                allowed=not config.fail_closed,
                decision=decision,
                input_payload=payload,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                timed_out=True,
                error=decision.reason,
            )

    def _matches(self, matcher: HookMatcher, event: str, payload: Dict[str, Any]) -> bool:
        if matcher.event and matcher.event != event:
            return False
        if matcher.tool_name:
            tool_name = str(payload.get("tool_name") or payload.get("tool", ""))
            if not fnmatch(tool_name, matcher.tool_name):
                return False
        if matcher.path_glob:
            paths = payload.get("paths") or payload.get("files") or []
            if isinstance(paths, str):
                paths = [paths]
            if not any(fnmatch(str(path), matcher.path_glob) for path in paths):
                return False
        for key, expected in matcher.metadata.items():
            if payload.get("metadata", {}).get(key) != expected:
                return False
        return True

    def _decision_from_output(self, output: Dict[str, Any]) -> HookDecision:
        if "decision" in output and isinstance(output["decision"], dict):
            return HookDecision(**output["decision"])
        if output.get("block") is True:
            return HookDecision(
                action=HookDecisionAction.BLOCK,
                reason=str(output.get("reason", "Blocked by hook")),
            )
        if output.get("payload_updates") and isinstance(output["payload_updates"], dict):
            return HookDecision(
                action=HookDecisionAction.MODIFY,
                payload_updates=output["payload_updates"],
                reason=str(output.get("reason", "")),
            )
        if output.get("warnings"):
            return HookDecision(
                action=HookDecisionAction.WARN,
                warnings=list(output.get("warnings", [])),
                reason=str(output.get("reason", "")),
            )
        return HookDecision()

    def _json_or_text(self, text: str) -> Dict[str, Any]:
        stripped = text.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"text": text}

    def _event_name(self, event: HookEvent | str) -> str:
        return event.value if isinstance(event, HookEvent) else event
