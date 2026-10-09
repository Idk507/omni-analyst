from __future__ import annotations

import os
from pathlib import Path


ALIASES = {
    "AI_FOUNDRY_PROJECT_ENDPOINT": "AZURE_OPENAI_ENDPOINT",
    "AI_FOUNDRY_API_KEY": "AZURE_OPENAI_API_KEY",
    "AI_FOUNDRY_DEPLOYMENT_NAME": "AZURE_OPENAI_DEPLOYMENT",
    "AI_FOUNDRY_API_VERSION": "AZURE_OPENAI_API_VERSION",
    "embedding_model": "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
}


def load_env_file(path: str | Path = ".env") -> dict[str, str]:
    """Load simple KEY=VALUE pairs from .env without requiring python-dotenv."""

    env_path = Path(path)
    if not env_path.exists():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _clean_value(value)
        if key and key not in os.environ:
            os.environ[key] = value
        loaded[key] = os.environ.get(key, value)

    for source, target in ALIASES.items():
        if target not in os.environ and source in os.environ:
            os.environ[target] = os.environ[source]
            loaded[target] = os.environ[source]
    return loaded


def azure_openai_env_status() -> dict[str, str | bool]:
    load_env_file()
    return {
        "endpoint_configured": bool(os.getenv("AZURE_OPENAI_ENDPOINT")),
        "api_key_configured": bool(os.getenv("AZURE_OPENAI_API_KEY")),
        "deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
        "api_version": os.getenv("AZURE_OPENAI_API_VERSION", ""),
        "embedding_deployment": os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", ""),
    }


def _clean_value(value: str) -> str:
    cleaned = value.strip()
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0].strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1]
    return cleaned.strip()
