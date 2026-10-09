from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Dict


DEFAULT_SETTINGS: Dict[str, Any] = {
    "active_provider_id": "azure_openai",
    "active_model": None,
    "theme": "system",
    "default_mode": "operator",
    "telemetry_opt_in": False,
}


class SettingsStore:
    """Local-first JSON-backed settings used by the operator UI."""

    def __init__(self, storage_path: str | None = None) -> None:
        self._lock = RLock()
        self._path = Path(
            storage_path
            or os.getenv("OMNI_SETTINGS_PATH", ".omni_memory/settings.json")
        )
        self._settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._load()

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def update(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for key, value in payload.items():
                if key in DEFAULT_SETTINGS:
                    self._settings[key] = value
            self._persist()
            return dict(self._settings)

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            self._settings = dict(DEFAULT_SETTINGS)
            self._persist()
            return dict(self._settings)

    def active_provider_id(self) -> str:
        return str(self._settings.get("active_provider_id") or "azure_openai")

    def active_model(self) -> str | None:
        value = self._settings.get("active_model")
        return str(value) if value else None

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in DEFAULT_SETTINGS:
                    self._settings[key] = value

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._settings, indent=2), encoding="utf-8")
