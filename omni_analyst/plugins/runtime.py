from __future__ import annotations

import json
import hashlib
import hmac
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from omni_analyst.hooks.runtime import HookRuntime
from omni_analyst.models.contracts import (
    HookCommand,
    HookConfig,
    HookMatcher,
    PluginActivation,
    PluginCapability,
    PluginCapabilityType,
    PluginPermission,
    PluginTrustLevel,
    SkillDefinition,
    ToolRegistration,
    SandboxCommand,
)
from omni_analyst.sandbox.executor import SandboxExecutor
from omni_analyst.skills.runtime import SkillRuntime


@dataclass
class PluginManifest:
    plugin_id: str
    name: str
    description: str = ""
    version: str = "0.1.0"
    author: str = ""
    root: str = ""
    namespace: str = ""
    trust_level: str = PluginTrustLevel.PROJECT.value
    permissions: List[Dict[str, Any]] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    hooks: List[str] = field(default_factory=list)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    mcp_servers: List[Dict[str, Any]] = field(default_factory=list)
    model_providers: List[Dict[str, Any]] = field(default_factory=list)
    web_search_providers: List[Dict[str, Any]] = field(default_factory=list)
    channels: List[Dict[str, Any]] = field(default_factory=list)
    subagents: List[Dict[str, Any]] = field(default_factory=list)
    capabilities: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)


class PluginRuntime:
    """Manifest-based plugin discovery and activation inspired by Claude/Codex."""

    def __init__(
        self,
        *,
        hook_runtime: HookRuntime | None = None,
        skill_runtime: SkillRuntime | None = None,
        tool_gateway: Any | None = None,
        sandbox_executor: SandboxExecutor | None = None,
        install_root: str | Path = ".omni_plugins",
        trust_secret: str | None = None,
    ) -> None:
        self.hook_runtime = hook_runtime
        self.skill_runtime = skill_runtime
        self.tool_gateway = tool_gateway
        self.sandbox_executor = sandbox_executor or SandboxExecutor("plugin-sandbox")
        self.install_root = Path(install_root)
        self.trust_secret = trust_secret
        self._manifests: dict[str, PluginManifest] = {}
        self._activations: dict[str, PluginActivation] = {}
        self._marketplace_index: dict[str, dict[str, Any]] = {}

    def install(
        self,
        package: str | Path,
        *,
        expected_sha256: str | None = None,
        overwrite: bool = False,
    ) -> PluginManifest:
        source = Path(package)
        if not source.exists():
            raise FileNotFoundError(f"Plugin package not found: {source}")
        digest = self._sha256(source)
        if expected_sha256 and digest != expected_sha256:
            raise ValueError("Plugin package checksum mismatch")
        self.install_root.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            target = self.install_root / source.name
            if target.exists() and overwrite:
                shutil.rmtree(target)
            if not target.exists():
                shutil.copytree(source, target)
        elif source.suffix == ".zip":
            target = self.install_root / source.stem
            if target.exists() and overwrite:
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source) as archive:
                archive.extractall(target)
        else:
            raise ValueError("Plugin install supports directories and .zip archives")
        discovered = self.discover(target)
        if not discovered:
            raise ValueError("No plugin manifest found after install")
        return discovered[0]

    def discover(self, root: str | Path) -> List[PluginManifest]:
        root_path = Path(root)
        manifests = []
        for manifest_path in list(root_path.glob("**/.claude-plugin/plugin.json")) + list(
            root_path.glob("**/.codex-plugin/plugin.json")
        ):
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            plugin_root = manifest_path.parent.parent
            name = payload.get("name", plugin_root.name)
            plugin_id = payload.get("id", name)
            namespace = payload.get("namespace", plugin_id.replace(" ", "_").lower())
            manifest = PluginManifest(
                plugin_id=plugin_id,
                name=name,
                description=payload.get("description", ""),
                version=payload.get("version", "0.1.0"),
                author=payload.get("author", ""),
                root=str(plugin_root),
                namespace=namespace,
                trust_level=payload.get("trust_level", PluginTrustLevel.PROJECT.value),
                permissions=self._permissions(payload.get("permissions", [])),
                skills=self._relative_files(plugin_root, ["skills", "commands"]),
                hooks=self._relative_files(plugin_root, ["hooks"]),
                tools=self._tools(payload.get("tools", []), namespace),
                diagnostics=[],
                commands=self._relative_files(plugin_root, ["commands"]),
                mcp_servers=self._named_capabilities(payload.get("mcp_servers", []), namespace),
                model_providers=self._named_capabilities(
                    payload.get("model_providers", payload.get("providers", [])),
                    namespace,
                ),
                web_search_providers=self._named_capabilities(payload.get("web_search_providers", []), namespace),
                channels=self._named_capabilities(payload.get("channels", []), namespace),
                subagents=self._named_capabilities(payload.get("subagents", payload.get("agents", [])), namespace),
            )
            manifest.capabilities = [
                capability.model_dump(mode="json")
                for capability in self._capabilities(manifest)
            ]
            manifest.diagnostics = self._validate(manifest)
            self._manifests[manifest.plugin_id] = manifest
            manifests.append(manifest)
        return manifests

    def activate(self, plugin_id: str) -> PluginActivation:
        if plugin_id not in self._manifests:
            raise KeyError(f"Unknown plugin: {plugin_id}")
        manifest = self._manifests[plugin_id]
        self._enforce_activation_policy(manifest)
        loaded_hooks: list[str] = []
        loaded_tools: list[str] = []
        loaded_skills: list[str] = []
        root = Path(manifest.root)

        if self.skill_runtime is not None:
            for relative in manifest.skills:
                path = root / relative
                if path.name == "SKILL.md":
                    skill = self.skill_runtime.register(
                        self.skill_runtime.load_file(path, root, manifest.namespace)
                    )
                    loaded_skills.append(skill.skill_id)

        if self.hook_runtime is not None:
            for relative in manifest.hooks:
                if not relative.endswith(".json"):
                    continue
                payload = json.loads((root / relative).read_text(encoding="utf-8"))
                hook_payloads = payload if isinstance(payload, list) else payload.get("hooks", [])
                for hook_payload in hook_payloads:
                    config = HookConfig(
                        event=hook_payload["event"],
                        name=f"{manifest.namespace}:{hook_payload.get('name', relative)}",
                        matcher=HookMatcher(**hook_payload.get("matcher", {})),
                        priority=hook_payload.get("priority", 100),
                        fail_closed=hook_payload.get("fail_closed", False),
                        source=f"plugin:{manifest.plugin_id}",
                        command=HookCommand(**hook_payload["command"])
                        if hook_payload.get("command")
                        else None,
                    )
                    self.hook_runtime.register_config(config)
                    loaded_hooks.append(config.hook_id)

        if self.tool_gateway is not None:
            for tool_payload in manifest.tools:
                registration = ToolRegistration(**tool_payload)
                if tool_payload.get("command"):
                    self.tool_gateway.register_local_tool(
                        registration.tool_name,
                        self._executable_tool(manifest, tool_payload),
                        metadata=registration,
                    )
                    loaded_tools.append(registration.tool_name)
                    continue
                if hasattr(self.tool_gateway, "register_tool_metadata"):
                    self.tool_gateway.register_tool_metadata(registration)
                loaded_tools.append(registration.tool_name)

        activation = PluginActivation(
            plugin_id=manifest.plugin_id,
            active=True,
            namespace=manifest.namespace,
            loaded_hooks=loaded_hooks,
            loaded_tools=loaded_tools,
            loaded_skills=loaded_skills,
            diagnostics=manifest.diagnostics,
        )
        self._activations[plugin_id] = activation
        return activation

    def deactivate(self, plugin_id: str) -> PluginActivation:
        if plugin_id not in self._activations:
            raise KeyError(f"Plugin is not active: {plugin_id}")
        activation = self._activations[plugin_id]
        if self.hook_runtime is not None:
            for hook_id in activation.loaded_hooks:
                if hasattr(self.hook_runtime, "unregister"):
                    self.hook_runtime.unregister(hook_id)
        if self.tool_gateway is not None:
            for tool_name in activation.loaded_tools:
                if hasattr(self.tool_gateway, "unregister_tool"):
                    self.tool_gateway.unregister_tool(tool_name)
        updated = PluginActivation(
            plugin_id=activation.plugin_id,
            active=False,
            namespace=activation.namespace,
            loaded_hooks=activation.loaded_hooks,
            loaded_tools=activation.loaded_tools,
            loaded_skills=activation.loaded_skills,
            diagnostics=activation.diagnostics,
        )
        self._activations[plugin_id] = updated
        return updated

    def list_plugins(self) -> List[PluginManifest]:
        return list(self._manifests.values())

    def list_activations(self) -> List[PluginActivation]:
        return list(self._activations.values())

    def list_capabilities(
        self,
        *,
        plugin_id: str | None = None,
        capability_type: str | None = None,
        active_only: bool = False,
    ) -> List[PluginCapability]:
        capabilities: list[PluginCapability] = []
        active_plugins = {
            activation.plugin_id
            for activation in self._activations.values()
            if activation.active
        }
        for manifest in self._manifests.values():
            if plugin_id and manifest.plugin_id != plugin_id:
                continue
            if active_only and manifest.plugin_id not in active_plugins:
                continue
            for item in manifest.capabilities:
                capability = PluginCapability.model_validate(item)
                if capability_type and capability.type.value != capability_type:
                    continue
                capabilities.append(capability)
        return sorted(capabilities, key=lambda item: (item.plugin_id, item.type.value, item.name))

    def autonomous_inventory(self) -> Dict[str, Any]:
        capabilities = self.list_capabilities()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for capability in capabilities:
            grouped.setdefault(capability.type.value, []).append(capability.model_dump(mode="json"))
        return {
            "plugins": [manifest.__dict__ for manifest in self.list_plugins()],
            "activations": [activation.model_dump(mode="json") for activation in self.list_activations()],
            "capabilities": grouped,
            "summary": {
                "plugin_count": len(self._manifests),
                "active_plugin_count": len([a for a in self._activations.values() if a.active]),
                "capability_count": len(capabilities),
            },
        }

    def load_marketplace(self, index_path: str | Path) -> Dict[str, Any]:
        payload = json.loads(Path(index_path).read_text(encoding="utf-8"))
        plugins = payload.get("plugins", [])
        self._marketplace_index = {item["id"]: item for item in plugins}
        return {"plugins": plugins, "count": len(plugins)}

    def marketplace(self) -> List[Dict[str, Any]]:
        return sorted(self._marketplace_index.values(), key=lambda item: item.get("id", ""))

    def sign_manifest(self, manifest_path: str | Path, secret: str | None = None) -> str:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        payload.pop("signature", None)
        signature = hmac.new(
            (secret or self.trust_secret or "").encode("utf-8"),
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["signature"] = {"algorithm": "hmac-sha256", "value": signature}
        Path(manifest_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return signature

    def _relative_files(self, root: Path, folders: List[str]) -> List[str]:
        files: list[str] = []
        for folder in folders:
            path = root / folder
            if not path.exists():
                continue
            files.extend(
                item.relative_to(root).as_posix()
                for item in path.rglob("*")
                if item.is_file()
            )
        return files

    def _permissions(self, raw: Any) -> List[Dict[str, Any]]:
        if isinstance(raw, dict):
            raw = [{"name": key, **(value if isinstance(value, dict) else {"allowed": bool(value)})} for key, value in raw.items()]
        permissions: list[dict[str, Any]] = []
        for item in raw or []:
            permission = PluginPermission(**item) if isinstance(item, dict) else PluginPermission(name=str(item))
            permissions.append(permission.model_dump())
        return permissions

    def _tools(self, raw: Any, namespace: str) -> List[Dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for item in raw or []:
            if isinstance(item, str):
                item = {"tool_name": f"{namespace}:{item}", "source": "plugin", "namespace": namespace}
            else:
                item = dict(item)
                item["tool_name"] = str(item.get("tool_name") or item.get("name") or "tool")
                if ":" not in item["tool_name"]:
                    item["tool_name"] = f"{namespace}:{item['tool_name']}"
                item.setdefault("source", "plugin")
                item.setdefault("namespace", namespace)
            registration = ToolRegistration.model_validate(item)
            normalized = registration.model_dump(mode="json")
            for extra in ["command", "timeout_seconds", "env_allowlist"]:
                if extra in item:
                    normalized[extra] = item[extra]
            tools.append(normalized)
        return tools

    def _named_capabilities(self, raw: Any, namespace: str) -> List[Dict[str, Any]]:
        if isinstance(raw, dict):
            raw = [{"name": key, **(value if isinstance(value, dict) else {})} for key, value in raw.items()]
        capabilities: list[dict[str, Any]] = []
        for item in raw or []:
            if isinstance(item, str):
                item = {"name": item}
            payload = dict(item)
            name = str(payload.get("name") or payload.get("id") or payload.get("graph_id") or "capability")
            if ":" not in name:
                name = f"{namespace}:{name}"
            payload["name"] = name
            payload.setdefault("namespace", namespace)
            capabilities.append(payload)
        return capabilities

    def _capabilities(self, manifest: PluginManifest) -> List[PluginCapability]:
        capabilities: list[PluginCapability] = []
        capabilities.extend(
            self._capability(
                manifest,
                PluginCapabilityType.TOOL,
                item["tool_name"],
                description=item.get("description", ""),
                risk_tier=item.get("risk_tier", "R0"),
                metadata=item,
            )
            for item in manifest.tools
        )
        capabilities.extend(
            self._capability(
                manifest,
                PluginCapabilityType.SKILL,
                Path(path).stem if Path(path).name != "SKILL.md" else Path(path).parent.name,
                source_path=path,
            )
            for path in manifest.skills
            if path.endswith("SKILL.md")
        )
        capabilities.extend(
            self._capability(
                manifest,
                PluginCapabilityType.HOOK,
                Path(path).stem,
                source_path=path,
            )
            for path in manifest.hooks
            if path.endswith(".json")
        )
        capabilities.extend(
            self._capability(
                manifest,
                PluginCapabilityType.COMMAND,
                Path(path).stem,
                source_path=path,
            )
            for path in manifest.commands
        )
        for capability_type, items in [
            (PluginCapabilityType.MCP_SERVER, manifest.mcp_servers),
            (PluginCapabilityType.MODEL_PROVIDER, manifest.model_providers),
            (PluginCapabilityType.WEB_SEARCH_PROVIDER, manifest.web_search_providers),
            (PluginCapabilityType.CHANNEL, manifest.channels),
            (PluginCapabilityType.SUBAGENT, manifest.subagents),
        ]:
            capabilities.extend(
                self._capability(
                    manifest,
                    capability_type,
                    item["name"],
                    description=item.get("description", ""),
                    metadata=item,
                )
                for item in items
            )
        capabilities.extend(
            self._capability(
                manifest,
                PluginCapabilityType.PERMISSION,
                item.get("name", "permission"),
                enabled=item.get("allowed", True),
                metadata=item,
            )
            for item in manifest.permissions
        )
        return capabilities

    def _capability(
        self,
        manifest: PluginManifest,
        capability_type: PluginCapabilityType,
        name: str,
        *,
        description: str = "",
        enabled: bool = True,
        risk_tier: str = "R0",
        source_path: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> PluginCapability:
        capability_name = name if ":" in name else f"{manifest.namespace}:{name}"
        return PluginCapability(
            capability_id=f"{manifest.plugin_id}:{capability_type.value}:{capability_name}",
            plugin_id=manifest.plugin_id,
            type=capability_type,
            name=capability_name,
            namespace=manifest.namespace,
            description=description,
            enabled=enabled,
            risk_tier=risk_tier,
            source_path=source_path,
            metadata=metadata or {},
        )

    def _validate(self, manifest: PluginManifest) -> List[str]:
        diagnostics: list[str] = []
        if not manifest.name:
            diagnostics.append("Plugin name is missing")
        if manifest.trust_level == PluginTrustLevel.UNTRUSTED.value and manifest.tools:
            diagnostics.append("Untrusted plugin declares tools; activation should be policy gated")
        names = [(item["type"], item["name"]) for item in manifest.capabilities]
        if len(names) != len(set(names)):
            diagnostics.append("Plugin declares duplicate capability names")
        return diagnostics

    def _enforce_activation_policy(self, manifest: PluginManifest) -> None:
        if manifest.trust_level == PluginTrustLevel.UNTRUSTED.value and manifest.tools:
            raise PermissionError("Untrusted plugins cannot activate tool capabilities")
        if self.trust_secret:
            manifest_path = self._manifest_path(Path(manifest.root))
            if manifest_path is None or not self._verify_manifest_signature(manifest_path):
                raise PermissionError("Plugin manifest signature verification failed")

    def _manifest_path(self, root: Path) -> Path | None:
        for relative in [".claude-plugin/plugin.json", ".codex-plugin/plugin.json"]:
            path = root / relative
            if path.exists():
                return path
        return None

    def _verify_manifest_signature(self, manifest_path: Path) -> bool:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        signature = payload.pop("signature", {})
        expected = hmac.new(
            (self.trust_secret or "").encode("utf-8"),
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(str(signature.get("value", "")), expected)

    def _executable_tool(self, manifest: PluginManifest, tool_payload: Dict[str, Any]):
        command = list(tool_payload.get("command") or [])
        timeout = int(tool_payload.get("timeout_seconds", 30))
        env_allowlist = set(tool_payload.get("env_allowlist", []))
        cwd = str(Path(manifest.root).resolve())

        def _run(**kwargs) -> Dict[str, Any]:
            env = {
                key: str(value)
                for key, value in kwargs.get("env", {}).items()
                if key in env_allowlist
            }
            result = self.sandbox_executor.run(
                SandboxCommand(
                    command=command + [str(value) for value in kwargs.get("args", [])],
                    cwd=cwd,
                    env=env,
                    timeout_seconds=timeout,
                )
            )
            return result.model_dump(mode="json")

        return _run

    def _sha256(self, source: Path) -> str:
        digest = hashlib.sha256()
        if source.is_dir():
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                digest.update(path.relative_to(source).as_posix().encode("utf-8"))
                digest.update(path.read_bytes())
        else:
            digest.update(source.read_bytes())
        return digest.hexdigest()
