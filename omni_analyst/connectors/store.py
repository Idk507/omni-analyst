from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List


class ConnectorStore:
    """Persisted user connectors (MCP servers) with secure secret references via env vars."""

    def __init__(self, storage_path: str | None = None) -> None:
        self._lock = RLock()
        self._path = Path(
            storage_path
            or os.getenv("OMNI_CONNECTORS_PATH", ".omni_memory/connectors.json")
        )
        self._connectors: List[Dict[str, Any]] = []
        self._load()

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [self._with_status(connector) for connector in self._connectors]

    def get(self, connector_id: str) -> Dict[str, Any]:
        with self._lock:
            for connector in self._connectors:
                if connector["id"] == connector_id:
                    return self._with_status(connector)
        raise KeyError(f"Unknown connector: {connector_id}")

    def upsert(self, connector: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            connector_id = str(connector.get("id") or connector.get("name") or "").strip()
            if not connector_id:
                raise ValueError("Connector requires an id or name")
            payload = {
                "id": connector_id,
                "name": str(connector.get("name") or connector_id),
                "category": str(connector.get("category") or "custom"),
                "description": str(connector.get("description") or ""),
                "url": str(connector.get("url") or ""),
                "transport": str(connector.get("transport") or "streamable_http"),
                "auth": connector.get("auth") or {"type": "none", "env_var": None},
                "enabled": bool(connector.get("enabled", True)),
                "tools": list(connector.get("tools") or []),
                "created_at": connector.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            for index, existing in enumerate(self._connectors):
                if existing["id"] == connector_id:
                    self._connectors[index] = payload
                    self._persist()
                    return self._with_status(payload)
            self._connectors.append(payload)
            self._persist()
            return self._with_status(payload)

    def remove(self, connector_id: str) -> bool:
        with self._lock:
            before = len(self._connectors)
            self._connectors = [item for item in self._connectors if item["id"] != connector_id]
            if len(self._connectors) != before:
                self._persist()
                return True
            return False

    def to_mcp_server_configs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "name": connector["id"],
                    "url": connector["url"],
                    "transport": connector["transport"],
                    "interceptor_names": ["state_bridge", "audit_logger"],
                    "headers": {},
                    "tools": connector.get("tools", []),
                }
                for connector in self._connectors
                if connector.get("enabled") and connector.get("url")
            ]

    def _with_status(self, connector: Dict[str, Any]) -> Dict[str, Any]:
        env_var = (connector.get("auth") or {}).get("env_var")
        secret_present = bool(env_var and os.getenv(env_var))
        payload = dict(connector)
        payload["secret_configured"] = secret_present
        payload["status"] = (
            "connected" if (connector.get("enabled") and (secret_present or not env_var)) else "needs_setup"
        )
        return payload

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, list):
            self._connectors = [item for item in payload if isinstance(item, dict)]

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._connectors, indent=2), encoding="utf-8")
